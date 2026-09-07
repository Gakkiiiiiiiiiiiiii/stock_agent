"""Explicit schema-migration owner; never invoked by the API process."""
from storage.bootstrap import create_all

if __name__ == "__main__":
    create_all()
