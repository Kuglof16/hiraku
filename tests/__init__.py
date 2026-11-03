import os

# The tests (and the scripted runs they start) may take the tools' check results from the disk cache
# kept for the same code (K2: runs/.check_cache). A real HIRAKU run never sets this and checks every time.
os.environ.setdefault("HIRAKU_CHECK_CACHE", "1")
