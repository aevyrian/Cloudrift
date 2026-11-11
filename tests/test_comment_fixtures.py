from app.comment_accounts import cookie_fingerprint


COOKIE = 'uid_tt=synthetic-primary; sessionid=synthetic-session'
CLIENT = 'c' * 32


def setup_comment_accounts(store):
    accounts = store.comment_accounts
    salt = accounts.status()['identity_salt']
    identities = {'primary': cookie_fingerprint(COOKIE, salt),
                  'secondary': cookie_fingerprint('uid_tt=synthetic-secondary; sessionid=synthetic-session', salt)}
    store.set_setting('cookie', COOKIE)
    accounts.bind(accounts.bind_start('secondary')['nonce'], identities['secondary'])
    accounts.resume(True)
    return identities
