import couchdbkit
import redis
import config
import Crypto.PublicKey.RSA as RSA
import Crypto.Hash.SHA256 as SHA256
import os
import json
import lua

import publicverifier


def HaikuWorker():
    def __init__(key, db):
        # Creates the haiku
        pass


def redeem_haiku(issuance, key, topic='kittens'):
    docid = SHA256.new(issuance['body']).hexdigest()
    docid = SHA256.new(docid + ':0').hexdigest()
    body = {
        'input_docs': {'in_0': docid},
        'pubkey': key.publickey().exportKey(),
        'topic': topic,
        'update': json.loads(issuance['body'])['update_code'],
        }
    body = json.dumps(body)
    signature = key.sign(body, None)
    tx = dict(body=body, signature=signature)
    verifier.submit(tx)
    return tx


def issue_haiku(issuekey, key):
    issueaddress = SHA256.new(issuekey.publickey().exportKey()).hexdigest()
    address = SHA256.new(key.publickey().exportKey()).hexdigest()
    schema = "GiftCheck Script 0.1"
    kind = "Haiku 0.4nsd"
    validate_code = """(function (rx, txdigest, txsig, tx)
        assert(str(tx.update) == str(rx.update_code),
               'update_code must match');
        assert(hexdigest(tx.pubkey) == str(rx.address), 'pubkey must match');
        validatesig(tx.pubkey, txdigest, txsig);
    end)"""
    update_code = ("""function (inputs, tx)
        assert(len(inputs) == 1, 'len(inputs) must be 1');
        docid = iter(inputs).next();
        s = inputs[docid];
        if not has_key(s, 'topic') then
            assert(has_key(tx, 'topic') == True, 'must include topic')
            s.topic = tx.topic;
            s.address = s.issue_address;
        elseif not has_key(s, 'haiku') then
            assert(has_key(tx, 'haiku'), 'must include haiku');
            s.haiku = tx.haiku;
            s.pop('validate');
            s.pop('address');
        end
        setinput(docid, s);
    end
    """)
    body = {"schema": schema,
            "kind": kind,
            "nonce": SHA256.new(os.urandom(20)).hexdigest(),
            "input_docs": {},
            "update_code": update_code,
            "validate_code": validate_code,
            "issue_address": issueaddress,
            "address": address,
            "new_docs": 1,
            "update": """(function(inputs, tx)
                s = dict();
                s.schema = tx.schema;
                s.kind = tx.kind;
                s.address = tx.address;
                s.update_code = tx.update_code;
                s.validate = tx.validate_code;
                setoutput(0, s);
            end)"""}
    body = json.dumps(body)
    signature = key.sign(SHA256.new(body).digest(), None)
    issuance = dict(body=body, signature=signature)
    verifier.submit(issuance)
    return issuance


def issue_bitcoin(key, balance=50):
    address = SHA256.new(key.publickey().exportKey()).hexdigest()
    schema = "GiftCheck Script 0.1"
    kind = "Bitcoin 0.1abd"
    validate_code = """(function (rx, txdigest, txsig, tx)
        assert(tx.update == rx.update_code, 'update_code must match');
        assert(hexdigest(tx.pubkey) == rx.address, 'pubkey must match');
        validatesig(tx.pubkey, txdigest, txsig);
    end)"""
    update_code = """
    function (inputs, tx)
        var total = 0;
        for k, i in inputs
            var i = inputs[k];
            total += i.balance;
            i.balance = 0;
            i.pop('address');
            i.pop('validate');
            setinput(k, i);
        end

        for idx, o in pairs((var idx in tx.outputs)
            var o = tx.outputs[idx];
            assert(o.balance > 0, 'output balance must be positive');
            total -= o.balance;
            s = dict();
            s.schema = '{schema}';
            s.update_code = tx.update;
            s.kind = '{kind}';
            s.balance = o.balance;
            s.address = hexdigest(o.address);
            s.validate = o.validate;
            setoutput(idx, s);
        end
        assert(total == 0, 'balances are equal');
        return doc_out;
    end
    """.format(schema=schema, kind=kind)
    body = {"schema": schema,
            "kind": kind,
            "nonce": SHA256.new(os.urandom(20)).hexdigest(),
            "input_docs": {},
            "update_code": update_code,
            "new_docs": 1,
            "balance": 10,
            "address": address,
            "update": """(function(inputs, tx)
                s = dict();
                s.schema = tx.schema;
                s.kind = tx.kind;
                s.balance = tx.balance;
                s.address = hexdigest(tx.address);
                s.update_code = tx.update_code;
                s.validate = [[%s]];
                setoutput(0, s);
            end)""" % validate_code}
    body = json.dumps(body)
    signature = key.sign(SHA256.new(body).digest(), None)
    issuance = dict(body=body, signature=signature)
    verifier.submit(issuance)
    return issuance


def generate_key():
    global key
    key = RSA.generate(1024)
    db['haiku_key'] = key.exportKey()


if __name__ == '__main__':
    reload(publicverifier)
    disk = couchdbkit.Server(url=config.verifier.COUCHDB_URL)
    disk = disk[config.verifier.COUCHDB_DB]
    cache = redis.Redis(port=config.verifier.REDIS_PORT,
                        db=config.verifier.REDIS_DB)

    db = redis.Redis(port=config.haiku_worker.REDIS_PORT,
                     db=config.haiku_worker.REDIS_DB)

    if not cache.get('verifier_key'):
        cache['verifier_key'] = RSA.generate(1024).exportKey()
    vkey = RSA.importKey(cache['verifier_key'])

    if not db.get('haiku_key'):
        generate_key()
    key = RSA.importKey(db['haiku_key'])

    verifier = publicverifier.PublicVerifier(disk, cache, vkey)
