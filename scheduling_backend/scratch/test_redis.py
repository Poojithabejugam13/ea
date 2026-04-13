import os
import redis
from dotenv import load_dotenv

load_dotenv()

host = os.getenv("REDIS_HOST")
port = os.getenv("REDIS_PORT")
password = os.getenv("REDIS_PASSWORD")

print(f"Testing connection to {host}:{port}...")

try:
    # Try with SSL first
    r = redis.Redis(
        host=host,
        port=int(port),
        password=password,
        ssl=True,
        socket_connect_timeout=5
    )
    r.ping()
    print("✅ Redis connection with SSL successful!")
except Exception as e_ssl:
    print(f"❌ Redis connection with SSL failed: {e_ssl}")
    try:
        # Try without SSL
        r = redis.Redis(
            host=host,
            port=int(port),
            password=password,
            ssl=False,
            socket_connect_timeout=5
        )
        r.ping()
        print("✅ Redis connection WITHOUT SSL successful!")
    except Exception as e_no_ssl:
        print(f"❌ Redis connection WITHOUT SSL failed: {e_no_ssl}")
