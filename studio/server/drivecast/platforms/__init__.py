"""Platform adapters. Each platform (YouTube now, Facebook Pages later) plugs in
here with the same four abilities: identify the connected account, publish a
video file, read stats, and say which OAuth scopes it needs."""
from . import youtube

ADAPTERS = {"youtube": youtube}


def get(platform):
    try:
        return ADAPTERS[platform]
    except KeyError:
        raise ValueError(f"unknown platform {platform}")
