import os
import hashlib
import base64
import json
import Crypto.PublicKey.RSA as RSA
import gevent


def random_name(bytes=18):
    return base64.urlsafe_b64encode(os.urandom(bytes))


class Gateway(object):
    def __init__(self, db, service):
        self.db = db
        self.service = service

    def new_client(self, foaf={}):
        name = random_name()
        self.db.set('client:%s:foaf' % name, json.dumps(foaf))
        return self.get_client(name)

    def get_client(self, name):
        return Client(name, self)

    def validate_queue():
        pass

    def _update_view(self, txid):
        # Update the tx write-once log
        transaction = self.service.get_transaction(txid)
        tx = json.loads(transaction['payload'])

        mytx = {}
        mytx['payload'] = transaction['payload']
        mytx['signature'] = transaction['signature']

        issuance = None
        for i in tx['inputs']:
            itx = json.loads(self.db.hget('tx', i['tx']))
            if issuance is not None:
                assert itx['issuance'] == issuance
            issuance = itx['issuance']
            itx = json.loads(itx['payload'])
            mytx['pubkey'] = itx['outputs'][i['idx']]['pubkey']

        if issuance is None: issuance = txid
        mytx['issuance'] = issuance
        self.db.hset('tx', txid, json.dumps(mytx))

        def issuance():
            pass

        # Remove people's unused caps
        for i in tx['inputs']:
            itx = json.loads(self.db.hget('tx', i['tx']))
            itx = json.loads(itx['payload'])
            pubkey = itx['outputs'][i['idx']]['pubkey']
            ckey = self.db.hget('keysclients', pubkey)
            if not ckey:
                continue
            inkey = '%s:%s' % (i['tx'], i['idx'])
            self.db.hdel('client:%s:available' % ckey, inkey)

        # Add this transaction to user's history
        if 'pubkey' in mytx:
            ckey = self.db.hget('keysclients', mytx['pubkey'])
            if ckey:
                self.db.sadd('client:%s:mytx' % ckey, txid)

        # Update the client info
        for idx in range(len(tx['outputs'])):
            o = tx['outputs'][idx]
            pubkey = o['pubkey']
            ckey = self.db.hget('keysclients', pubkey)
            if not ckey:
                continue
            # Add this to the list of outputs
            outkey = '%s:%s' % (txid, idx)
            self.db.hset('client:%s:available' % ckey, outkey, json.dumps(o))

    def dump_views(self):
        # Drop all the views so we can rebuild them
        self.db.delete(*self.db.keys('client:*:available'))
        self.db.delete('lastseen', 'tx')
        self.db.delete(*self.db.keys('client:*:mytx'))

    def build_views(self):
        # Get the last seen
        lastseen = self.db.get('lastseen')
        if lastseen is None:
            lastseen = '-inf'
        for tx, score in self.service.db.zrangebyscore('order',
                                                       '('+lastseen, '+inf',
                                                       withscores=True):
            self._update_view(tx)
            self.db.set('lastseen', score)

    def _run(self):
        # Process all the views we haven't seen yet
        # Then subscribe
        try:
            for message in self.pubsub.listen():
                self._update_view(message['data'])
        finally:
            self.pubsub.unsubscribe('tx')

    def start(self):
        """
        View server
        """
        self.pubsub = self.service.db.pubsub()
        self.pubsub.subscribe('tx')
        self.build_views()
        self._thread = gevent.spawn(self._run)

    def stop(self):
        if hasattr(self, '_thread'):
            self._thread.kill()


class Client(object):
        def __init__(self, name, gateway):
            self.gateway = gateway
            self.name = name

        def get_data(self):
            db = self.gateway.db
            return db.hgetall('client:%s:available' % self.name)

        def get_foaf(self):
            db = self.gateway.db
            return json.loads(db.get('client:%s:foaf' % self.name))

        def new_key(self):
            db = self.gateway.db
            key = RSA.generate(1024)
            pubkey = key.publickey().exportKey()
            privkey = key.exportKey()
            db.hset('client:%s:wallet' % self.name, pubkey, privkey)
            db.hset('keysclients', pubkey, self.name)
            return pubkey

        def get_keys(self):
            db = self.gateway.db
            return db.hgetall('client:%s:wallet' % self.name)

        def _submit_transaction(self, payload, pubkey):
            # Look up the key and sign the payload with it
            db = self.gateway.db
            key = db.hget('client:%s:wallet' % self.name, pubkey)
            payload = json.dumps(payload)
            h = hashlib.sha256(payload)
            txid = h.hexdigest()
            K = RSA.Random.get_random_bytes(20)
            signature = RSA.importKey(key).sign(h.digest(), K)
            transaction = {
                'payload': payload,
                'signature': signature}
            service = self.gateway.service
            service.submit_transaction(transaction)
            return txid

        def issue_giftcheck(self, message, value):
            # Create a new key and store in our wallet
            pubkey = self.new_key()
            payload = {
                'inputs': [],
                'data': {
                    'txtype': 'issuance',
                    'schema': 'Apple GiftChecks 0.2f',
                    'message': message},
                'outputs': [
                    {'type': 'transfer',
                     'pubkey': pubkey,
                     'value': value}]}

            return self._submit_transaction(payload, pubkey)

        def transfer_giftcheck(self, txinput, value, dstkey):
            # Lookup the pubkey in the previous transaction
            tx = self.gateway.service.get_transaction(txinput['tx'])
            tx = json.loads(tx['payload'])
            txin = tx['outputs'][txinput['idx']]
            assert txin['type'] == 'transfer'
            pubkey = txin['pubkey']

            # Add an output with the new value
            invalue = txin['value']
            assert value <= invalue
            outputs = [{'type':'transfer',
                        'pubkey':dstkey,
                        'value': value}]

            # Make change if there's less
            if invalue > value:
                outputs.append({'type':'transfer',
                                'pubkey':pubkey,
                                'value':invalue-value})
            payload = {
                'data': {
                    'txtype':'transfer',
                    'schema': 'Apple GiftChecks 0.2f'},
                'inputs': [txinput],
                'outputs': outputs}

            return self._submit_transaction(payload, pubkey)
