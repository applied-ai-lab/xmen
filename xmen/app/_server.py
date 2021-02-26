import os
import argparse
from configparser import ConfigParser

from mysql.connector import MySQLConnection

from xmen.app._xgent import DESCRIPTION
from xmen.server import *

parser = argparse.ArgumentParser(prog='xmen-server', description=DESCRIPTION)
parser.add_argument('--host', '-H', default='', help='The host to run the xmen server on')
parser.add_argument('--port', '-P', default=8000, help='The port to run the xmen server on', type=int)
parser.add_argument('--certfile', '-C', default='/etc/letsencrypt/live/xmen.rob-otics.co.uk/fullchain.pem',
                    help='The path to the ssl certificate')
parser.add_argument('--dbconfig', '-D', default='/home/robw/config.ini')
parser.add_argument('--keyfile', '-K', default='/etc/letsencrypt/live/xmen.rob-otics.co.uk/privkey.pem')
parser.add_argument('--n_clients', '-N', default=100, help='The maximum number of client connections')


def server(args):
    from threading import Thread
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(
        certfile=args.certfile,
        keyfile=args.keyfile)
    s = socket.socket()
    s.bind((args.host, args.port))
    s.listen(args.n_clients)
    processes = []
    try:
        while True:
            conn, address = s.accept()
            p = Thread(
                target=ServerTask(
                    args.host, args.port, args.dbconfig,
                    args.certfile, args.keyfile, args.n_clients),
                args=(conn, address))
            p.start()
            processes += [p]
    finally:
        for p in processes:
            p.terminate()


class ServerTask(object):
    def __init__(
            self,
            host,
            port,
            config,
            certfile,
            keyfile,
            n_clients=100,
    ):
        self.host = host
        self.port = port
        self.n_clients = n_clients
        self._config = config
        self.certfile = certfile
        self.keyfile = keyfile

    def open_socket(self):
        """Open a socket and an ssl context"""
        import ssl
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(
            certfile=self.certfile,
            keyfile=self.keyfile,
        )
        # get the hostname
        s = socket.socket()
        print('Binding on', (self.host, self.port))
        s.bind((self.host, self.port))  # bind host address and port together
        return s, context

    def __call__(self, conn, addr):
        try:
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            context.load_cert_chain(
                certfile=self.certfile,
                keyfile=self.keyfile)
            conn = context.wrap_socket(conn, server_side=True)
            while True:
                # receive
                msg = receive(conn)
                if not msg:
                    # connection has been closed
                    break
                request = decode_request(msg)
                response = Failed('Request not recognised')
                if isinstance(request, ChangePassword):
                    print('Got Change Password Request')
                    response = self.change_password(request.user, request.password, request.new_password)
                elif isinstance(request, AddUser):
                    print('Got Add User Request')
                    response = self.register_user(request.user, request.password)
                elif isinstance(request, RegisterExperiment):
                    print('Got Register Experiment request')
                    response = self.register_experiment(
                        request.user, request.password, request.root, request.data)
                elif isinstance(request, UpdateExperiment):
                    print('Got Update Experiment Request')
                    response = self.update_experiment(
                        request.user, request.password, request.root, request.data, request.status)
                    print(response)
                send(response, conn)
        except Exception as m:
            print(f'An error occured: {m}')
            pass
        finally:
            if conn is not None:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                    conn.close()  # close the connection
                except (socket.error, OSError) as m:
                    print(m)

    @property
    def config(self, section='mysql'):
        """Return the database configuration file as a dict"""
        if not isinstance(self._config, dict):
            parser = ConfigParser()
            parser.read(self._config)
            config = {}
            if parser.has_section(section):
                items = parser.items(section)
                for item in items:
                    config[item[0]] = item[1]
            else:
                raise Exception('{0} not found in the {1} file'.format(section, self._config))
            self._config = config
        return self._config

    def database(self):
        return MySQLConnection(**self.config)

    def validate_password(self, user, password):
        from xmen.server import PasswordNotValid, PasswordValid, UserDoesNotExist
        database = self.database()
        cursor = database.cursor()
        try:
            cursor.execute(f"SELECT * FROM users WHERE user = '{user}'")
            matches = cursor.fetchall()
            if matches:
                valid = self.is_valid(password, matches[0][3])
                response = PasswordValid(user) if valid else PasswordNotValid(user)
            else:
                response = UserDoesNotExist(user)
        finally:
            cursor.close()
            database.close()
        return response

    def change_password(self, user, old, new):
        response = self.validate_password(user, old)
        database = self.database()
        cursor = database.cursor()
        try:
            if isinstance(response, PasswordValid):
                hashed, salt = self.hash_password(new)
                cursor.execute(f"UPDATE users SET password = {str(hashed)[1:]} WHERE user = '{user}'")
                database.commit()
                response = PasswordChanged(user)
            elif isinstance(response, PasswordNotValid):
                response = Failed(f'Password is not valid for {user}')
        except Exception as m:
            response = Failed(str(m))
            pass
        finally:
            cursor.close()
            database.close()
            return response

    def hash_password(self, password):
        import sys
        path = os.path.join(os.getenv('HOME'), '.xmen')
        if not path in sys.path:
            sys.path.append(path)
        from password import hash
        return hash(password)

    def is_valid(self, password, hashed):
        import sys
        path = os.path.join(os.getenv('HOME'), '.xmen')
        if not path in sys.path:
            sys.path.append(path)
        from password import check
        return check(password, hashed)

    def register_user(self, user, password):
        database = self.database()
        cursor = database.cursor()
        response = None
        try:
            response = self.validate_password(user, password)
            if isinstance(response, (PasswordValid, PasswordNotValid)):
                pass
            elif isinstance(response, UserDoesNotExist):
                print('user does not exist')
                hashed, salt = self.hash_password(password)
                cursor.execute(
                    f"INSERT INTO users(user, password, salt) VALUES('{user}',{str(hashed)[1:]},{str(salt)[1:]})")
                database.commit()
                response = UserCreated(user)
        except Exception as m:
            cursor.close()
            database.close()
            response = Failed(str(m))
        finally:
            cursor.close()
            database.close()
            return response

    def update_experiment(self, user, password, root, data, status):
        from xmen.experiment import DELETED
        database = self.database()
        cursor = database.cursor()
        response = None
        try:
            response = self.validate_password(user, password)
            if isinstance(response, PasswordNotValid):
                return Failed(f'{response.msg}')
            elif isinstance(response, Failed):
                return response
            else:
                # assume experiments previously at the same root have since been deleted]
                cursor.execute(
                    f"UPDATE experiments SET status = '{status}', data = '{data}', updated = CURRENT_TIMESTAMP() WHERE root = '{root}' AND status != '{DELETED}'")
                database.commit()
                response = ExperimentUpdated(user, root)
        except Exception as m:
            cursor.close()
            database.close()
            response = Failed(str(m))
        finally:
            cursor.close()
            database.close()
            return response

    def register_experiment(self, user, password, root, data):
        from xmen.experiment import REGISTERED, DELETED
        database = self.database()
        cursor = database.cursor()
        response = None
        try:
            response = self.validate_password(user, password)
            if isinstance(response, PasswordNotValid):
                return Failed(f'{response.msg}')
            elif isinstance(response, Failed):
                return response
            else:
                # assume experiments previously at the same root have since been deleted
                cursor.execute(
                    f"UPDATE experiments SET status = '{DELETED}' WHERE root = '{root}'")
                cursor.execute(
                    f"INSERT INTO experiments(root, status, user, data) VALUES('{root}','{REGISTERED}','{user}','{data}')"
                )
                database.commit()
                response = ExperimentRegistered(user, root)
        except Exception as m:
            cursor.close()
            database.close()
            response = Failed(str(m))
        finally:
            cursor.close()
            database.close()
            return response


if __name__ == '__main__':
    args = parser.parse_args()
    server(args)
