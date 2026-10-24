"""Fleet GUI: a read-only web dashboard for every GPU in the fleet.

The server polls each machine in the hub's hosts.conf over ssh, keeps the
latest snapshot and a short utilization history per GPU, and serves a static
page plus a small JSON API on localhost. Standard library only.
"""

__version__ = "1.0.0"
