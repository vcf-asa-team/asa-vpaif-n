import hashlib

password = "VMware123!VMware123!"
hashed_password = hashlib.sha512(password.encode('utf-8')).hexdigest()

print(hashed_password)