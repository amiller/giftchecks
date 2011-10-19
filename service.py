import hashlib
import json
import Crypto.PublicKey.RSA as RSA


class TrustedParty(object):
    """This guy interacts with the Redis with transactions.
    Submitting a transaction first makes a 'commit.'
    """
    def __init__(self, db):
        self.db = db

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

        raise Exception('Unkown type')

    def _verify_signature(self, signature, txid, pubkey):
        key = RSA.importKey(pubkey)
        key.validate(txid, signature)

    def _validate(self, inputs, data, outputs):
        """Do the value-specific
        """
        assert data['schema'] == 'Apple GiftChecks 0.2f'
        self._validate_applegiftcheck(inputs, data, outputs)

    def get_transaction(self, txid):
        return json.loads(self.db.get('tx:%s' % txid))

    def submit_transaction(self, transaction):
        """
        Args:
           transaction:
              signature
              content
        """
        signature = transaction['signature']
        payload = json.loads(transaction['payload'])
        txid = hashlib.sha256(transaction['payload']).hexdigest()
        inputs = payload['inputs']
        data = payload['data']
        outputs = payload['outputs']

        db = self.db

        with db.pipeline() as pipe:
            pubkey = None
            _inputs = []
            # Check that all the input commits are available
            for i in inputs:
                commit_key = 'commit:%s:%s' % (i['tx'], i['idx'])
                pipe.watch(commit_key)
                commit = pipe.get(commit_key)
                if commit is None:
                    raise Exception("Input is unavailable: %s" % i)
                if commit is not '':
                    raise Exception("Input already claimed: %s" % i)

                # Check that there's only one signature requirement
                txin = json.loads(pipe.get('tx:%s' % i['tx']))
                txin = json.loads(txin['payload'])
                txin = txin['outputs'][i['idx']]
                txpubkey = txin['pubkey']
                if pubkey is None:
                    pubkey = txpubkey
                assert pubkey == txpubkey, "Inconsistent input pubkeys"
                _inputs.append(txin)

            # Custom logic validation, depending on the tx type
            self._validate(_inputs, data, outputs)

            # Validate the signatures if any are present
            if pubkey:
                self._verify_signature(signature, payload, pubkey)

            # Commit everything
            pipe.watch('order')
            lastval = pipe.zcard('order')
            pipe.multi()
            for i in inputs:
                commit_key = 'commit:%s:%s' % (i['tx'], i['idx'])
                pipe.set(commit_key, txid)

            # Create new commit holders
            for idx in range(len(outputs)):
                commit_key = 'commit:%s:%s' % (txid, idx)
                pipe.set(commit_key, '')

            pipe.set('tx:%s' % txid, json.dumps(transaction))
            pipe.zadd('order', txid, lastval)
            pipe.execute()

        # Let any observers know about it
        self.db.publish('tx', txid)
