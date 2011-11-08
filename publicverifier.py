import json
import Crypto.PublicKey.RSA as RSA
import Crypto.Hash.SHA256 as SHA256
import os
import couchdbkit
import lua


def dict_has_key(d, k):
    return int(k in d)


def _validate_haikugiftcheck(self, inputs, data, outputs):
    return True


def _validate_applegiftcheck(self, inputs, data, outputs):
    if data['txtype'] == 'issuance':
        # An issuance creates a single transfer
        o, = outputs
        assert o['type'] == 'transfer'
        return True

    if data['txtype'] == 'transfer':
        inval = sum([i['value'] for i in inputs])
        outval = sum([o['value'] for o in outputs])
        assert inval == outval, 'input doesn\'t match output'

        for i in inputs:
            assert i['type'] == 'transfer'

        for o in outputs:
            assert o['type'] == 'transfer'
            assert 'pubkey' in o
            assert 'value' in o

        return True

    raise Exception('Unknown type')


class Publisher(object):
    def __init__(self, disk, cache):
        self.disk = disk
        self.cache = cache
        self.squeue = ('verifier_squeue')

    def _run(self):
        while True:
            work = self.cache.blpoprpush(self.squeue)
            self.disk


class PublicVerifier(object):
    """The PublicVerifier is a message verifier and timestamp server.
    It has a key it uses to sign valid messages it sees. Its special
    characteristic is that it is publicly verifiable. The private key
    is kept secret, but all its input and output is expected to
    withstand public scrutiny.

    The PublicVerifier works in conjunction with a PublicHashTable
    (PHT) which may also be a DistributedHashTable (DHT). The Public
    HashTable is relied on not to forget messages. It is only relied
    on to have 'eventual consistency' which is why we can use CouchDB.
    It's the PublicVerifier's job to make sure it doesn't sign something
    twice.

    Each message passed to authority.submit(message) is validated for soundness
    and checked against double spending (via the cache and disk). If it's
    valid, then the message is signed and committed.

    The cache is used to enforce atomicity and prevent double spending. So
    it's safe to create instances of PublicVerifier in several processes. The
    constraint is that the cache must be unique per-key.

    The caller is relied on not to consume too many resources, perhaps
    through a proof-of-work, account-authentication, or payment scheme.
    """
    def __init__(self, disk, cache, key):
        # Cache is a redis, disk is a couchdb
        # Key is a private key we use for signing
        self.cache = cache
        self.disk = disk
        self.key = key
        pubkey = self.key.publickey()
        self.queue = ('verifier_queue:' +
                      SHA256.new(pubkey.exportKey()).hexdigest())

    def _verify_signature(self, signature, digest, pubkey):
        key = RSA.importKey(pubkey)
        key.verify(digest, signature)

    def _bind_json(self, cx, name, obj):
        cx.eval_script('%s = %s;' % (name, json.dumps(obj)))

    def _context(self):
        def js_assert(condition, assertion=None):
            assert condition, assertion

        def js_clone(obj):
            return json.loads(json.dumps(obj))

        def js_hexdigest(s):
            return SHA256.new(s).hexdigest()

        def js_validatesig(key, digest, sig):
            RSA.importKey(key).validate(digest, sig)

        lua.globals().dict = dict
        lua.globals().len = len
        lua.globals().str = str
        lua.globals().iter = iter
        lua.globals().has_key = dict_has_key
        lua.globals()['assert'] = js_assert
        lua.globals().clone = js_clone
        lua.globals().hexdigest = js_hexdigest
        lua.globals().validatesig = js_validatesig

    def _validate_update(self, inputs, tx, txhexdigest):
        self._context()
        js_output = {}
        js_input = {}

        def js_setoutput(idx, s):
            k = '%s:%d' % (txhexdigest, idx)
            js_output[SHA256.new(k).hexdigest()] = s

        def js_setinput(k, s):
            js_input[k] = s

        lua.globals().setoutput = js_setoutput
        lua.globals().setinput = js_setinput
        f = lua.eval(tx['update'])
        f(inputs, tx)
        return js_input, js_output

    def _validate_input(self, rx, txdigest, txsig, tx):
        """Do the value-specific validation
        """
        digest = SHA256.new(rx['rxbody']).digest()
        self.key.validate(rx['signature'], digest)
        body = json.loads(rx['rxbody'])

        self._context()
        lua.eval(body['validate'])(body, digest, txsig, tx)
        return True

    def submit(self, transaction):
        self.cache.lpush(self.queue, json.dumps(transaction))

    def _run(self, once=False):
        work = ('verifier_work:%s:%s' %
                (SHA256.new(self.key.publickey().exportKey()).hexdigest(),
                 SHA256.new(os.urandom(20)).hexdigest()))
        while True:
            tx = self.cache.brpoplpush(self.queue, work)
            tx = json.loads(tx)
            ok = False
            try:
                self._process(work, tx)
                ok = True
            except AssertionError, e:
                print e
                ok = True
            finally:
                # Put it back if we had an error
                if not ok: self.cache.lpush(self.queue, json.dumps(tx))
            if once: break

    def _watch_and_get_rx(self, pipe, docid):
        cache_docid = 'verifier_doc:%s' % docid
        pipe.watch(cache_docid)
        rx = pipe.get(cache_docid)
        if not rx:
            try:
                rx = self.disk[cache_docid]
                pipe.set(cache_docid, rx)
            except couchdbkit.ResourceNotFound:
                return None
        return json.loads(rx)

    def _process(self, work, transaction):
        """
        Args:
           action

        What is the transaction telling us? What is it we're being asked
        to verify and not double-spend on something?
        """
        txbody, txsig = transaction['body'], transaction['signature']
        txdigest = SHA256.new(txbody).digest()
        txhexdigest = SHA256.new(txbody).hexdigest()
        txbody = json.loads(txbody)

        # What is the ticket asking us for?
        """Bindings is a mapping from names used in the script
        to receipts we can apply.
        """
        with self.cache.pipeline() as pipe:
            # For every input doc, get the newest receipt for that
            # doc. Each doc has to successfully validate this
            # transaction. Store each doc in a mapping so the
            # update script can use their values as the first
            # pass.
            input_docs = txbody['input_docs'] if 'input_docs' in txbody \
                         else {}
            input_scope = {}
            for var, docid in input_docs.iteritems():
                rx = self._watch_and_get_rx(pipe, docid)
                input_scope[var] = rx
            for var, rx in input_scope.iteritems():
                self._validate_input(rx, txdigest, txsig, txbody)

            # If the tx creates any new docs,
            # then we need to prepare those ahead of time
            new_docs = txbody['new_docs'] if 'new_docs' in txbody \
                       else 0
            for i in range(new_docs):
                docid = SHA256.new('%s:%d' % (txhexdigest, i)).hexdigest()
                assert self._watch_and_get_rx(pipe, docid) is None, \
                       'New doc already exists'

            # Apply the update function
            in_update, out_update = self._validate_update(input_scope,
                                                          txbody,
                                                          txhexdigest)
            print 'in_update:', in_update
            print 'out_update:', out_update
            pipe.multi()

            def push(docid, receipt):
                receipt = json.dumps(receipt)
                digest = SHA256.new(receipt).digest()
                signature = self.key.sign(digest, None)
                rx = {'rxbody': receipt, 'signature': signature}
                pipe.set('verifier_doc:%s' % docid, json.dumps(rx))
                pipe.rpush('verifier_squeue', rx)

            for i in range(new_docs):
                docid = SHA256.new('%s:%d' % (txhexdigest, i)).hexdigest()
                print 'new_doc:', docid, out_update[docid]
                push(docid, out_update[docid])

            for k, docid in input_docs.iteritems():
                print 'update_doc:', docid, in_update[k]
                push(docid, in_update[k])

            pipe.lpop(work)
            pipe.execute()
