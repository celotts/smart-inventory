class TokenInvalido(Exception): pass
class ErrorDePassword(Exception): pass
SECRET = "dev-secret"

def crear_access_token(*args, **kwargs):
    from datetime import datetime, timedelta
    payload = {}
    if args:
        data = args[0]
        if isinstance(data, str):
            payload['sub'] = str(data)
        elif isinstance(data, dict):
            for k,v in data.items():
                payload[k] = str(v)
    for k,v in kwargs.items():
        if k == 'expires_delta':
            continue
        payload[k] = str(v)
    exp_delta = kwargs.get('expires_delta')
    try:
        import jwt
    except:
        try:
            from jose import jwt
        except:
            import jwt
    expire = datetime.utcnow() + (exp_delta or timedelta(minutes=15))
    payload['exp'] = int(expire.timestamp())
    token = jwt.encode(payload, SECRET, algorithm='HS256')
    return token, expire

def crear_refresh_token(*args, **kwargs):
    return crear_access_token(*args, **kwargs)

def hashear_token(token): 
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()
def generar_password_temporal(longitud=12):
    import secrets, string
    return ''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(longitud))
def verificar_password(password, hash_pw):
    try:
        import bcrypt
        if isinstance(password, str): password = password.encode('utf-8')
        if isinstance(hash_pw, str): hash_pw = hash_pw.encode('utf-8')
        return bcrypt.checkpw(password, hash_pw)
    except:
        return False
def obtener_password_hash(password):
    try:
        import bcrypt
        if isinstance(password, str): password = password.encode('utf-8')
        return bcrypt.hashpw(password, bcrypt.gensalt()).decode('utf-8')
    except:
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest()
hashear_password = obtener_password_hash
validar_password = lambda p: bool(p and len(p) >= 6)
def verificar_configuracion_de_secreto(): pass
def decodificar_token(token, token_type=None):
    try:
        try:
            import jwt
        except:
            from jose import jwt
        try:
            return jwt.decode(token, SECRET, algorithms=['HS256'])
        except:
            raise TokenInvalido()
    except TokenInvalido:
        raise
    except:
        raise TokenInvalido()
