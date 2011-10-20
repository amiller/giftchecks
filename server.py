# <Trading Game>
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
# 
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import json
import os
import os.path
import urlparse
from time import time
import argparse
import flask
from gevent.wsgi import WSGIServer
from werkzeug import SharedDataMiddleware
import redis
import gevent.monkey
gevent.monkey.patch_all()

import gateway
import service
reload(gateway)
reload(service)

if 'serv' not in globals(): serv = None
else: del serv
if 'gate' not in globals():
    gate = None
else: gate.stop(); del gate

app = None
base = os.path.dirname(__file__)

clients = {}


def startapp(args):
    global app, dbT, gate, serv, isue2

    def isue2(name='ndMw-fJkTb52jbUnv6QhFpc5'):
        client = gate.get_client(name)
        labnames = json.load(open('labnames.js'))
        for n,email in labnames.iteritems():
            avs = gate.db.hgetall('client:%s:available' % name)
            key = avs.keys()[0]
            tx,idx = key.split(':')
            tx = {'tx':tx, 'idx': int(idx)}
            dst = gate.new_client(foaf={'fullname':n, 'email':email})
            dstkey = dst.new_key()
            client.transfer_giftcheck(tx, 3, dstkey)
            print email, n, 'http://ln.soc1024.com:8191/a/%s/' % dst.name
            gevent.sleep(0.5)

    db_ = redis.Redis(port=args.redis_port, db=0)
    dbT = redis.Redis(port=args.redis_port, db=1)
    serv = service.TrustedParty(db_)
    gate = gateway.Gateway(dbT, serv)
    gate.start()
    
    app = flask.Flask(__name__, static_url_path='/')
    app.debug = True
    app.wsgi_app = SharedDataMiddleware(app.wsgi_app, {
        '/': os.path.join(base, 'static')})

    @app.route('/j/<account>/', methods=['GET'])
    def j(account):
        client = gate.get_client(account)
        return json.dumps(client.get_data())

    @app.route('/admin2309f/new_account/<fullname>')
    def new_account(fullname):
        A = gate.new_client(foaf={'fullname':fullname})
        A.issue_giftcheck("""I'm bringing 48 apples in total to give out at
        the lab meeting this Friday. This week I got a good deal on
        organic Gala apples from Orlando Organics,
        about $30 for the lot. They're sweeter but smaller than
        the ones from last week.""", 48)
        return flask.redirect('/a/%s/' % A.name)

    @app.route('/p/<name>/tx', methods=['POST'])
    def post(name):
        txinput = json.loads(flask.request.form['input'])
        value = int(flask.request.form['value'])
        foaf = json.loads(flask.request.form['foaf'])
        dst = gate.new_client(foaf)
        client = gate.get_client(name)
        client.transfer_giftcheck(txinput, value, dst.new_key())
        return dst.name

    @app.route('/a/<account>/', methods=['GET'])
    def main_a(account):
        # Check that the account is permitted to put
        client = gate.get_client(account)
        #data = client.get_data()
        db = gate.db
        name = client.name
        available = db.hgetall('client:%s:available' % name)
        _available = []
        total = 0
        for i, t in available.iteritems():
            t = json.loads(t)
            txid, idx = i.split(':')
            tx = json.loads(db.hget('tx', txid))
            tx['payload'] = json.loads(tx['payload'])
            t['tx'] = tx
            issuance = json.loads(db.hget('tx', tx['issuance']))
            issuance['payload'] = json.loads(issuance['payload'])
            t['issuance'] = issuance
            t['key'] = i
            t['txid'] = txid
            t['idx'] = int(idx)
            _available.append(t)
            total += t['value']
        mytxes = []
        events = []
        for txid in gate.db.smembers('client:%s:mytx' % client.name):
            tx = json.loads(gate.db.hget('tx', txid))
            tx['payload'] = json.loads(tx['payload'])
            print tx
            for o in tx['payload']['outputs']:
                pubkey = o['pubkey']
                value = o['value']
                ckey = gate.db.hget('keysclients', pubkey)
                if ckey == client.name:
                    continue
                if ckey:
                    foaf = json.loads(gate.db.get('client:%s:foaf' % ckey))
                else:
                    foaf = {'fullname':'anonymous', 'email':''}
                events.append({'value':value, 'foaf':foaf})
                    
        foaf = client.get_foaf()
        kwargs = {'available': _available,
                  'foaf': foaf,
                  'total': total,
                  'name': name,
                  'mytxes': mytxes,
                  'events': events,
                  'json':json}
        
        return flask.render_template('apple.htm', **kwargs)

    @app.route('/play/<id>/')
    def play(id):        
        with open(os.path.join(base, 'static', 'index.htm'), 'r') as fp:
            return fp.read()


if __name__ == '__main__':
    parser = argparse.ArgumentParser('GiftChecks')
    parser.add_argument('--port', type=int, default=8191)
    parser.add_argument('--redis-port', type=int, default=8192)
    args = parser.parse_args()
    
    startapp(args)

    print 'Serving on port', args.port
    if app.debug and 0:
        app.run('0.0.0.0', args.port)
    else:
        http_server = WSGIServer(('', args.port), app)
        http_server.serve_forever()
