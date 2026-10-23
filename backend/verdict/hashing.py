from hashlib import sha256 as _sha256

def sha256(path):
    digest=_sha256()
    with open(path,'rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):digest.update(block)
    return digest.hexdigest()
