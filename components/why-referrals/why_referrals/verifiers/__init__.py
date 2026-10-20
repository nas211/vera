"""Proof-of-task backends, and an honest map of what each one can actually see.

THE POINT OF THIS PACKAGE IS THE PROBE. Every quest reward depends on whether
we can check the task for free, and the answer is different per platform and
per action:

  X reply / quote / post   FREE. publish.twitter.com/oembed returns the author
                           and the text of any public post, unauthenticated.
                           This is the strong path, so the rewards lean on it.
  Instagram comment        FREE with the IG Graph token we already own — the
                           comment list on OUR OWN media names the commenters.
  X like / repost          NOT READABLE for free. `liking_users` needs a paid
                           API tier. Without one these are ATTESTED: the user
                           says they did it, we pay a fraction, and a spot
                           check can take it back.
  Follows                  Same as likes.
  Instagram likes          Not available at any price — Graph gives counts, not
                           who. Apify can scrape it if the token is funded.

Nothing here ever returns "verified" because a call failed. A verifier that
cannot reach its API returns UNKNOWN, and an UNKNOWN claim waits for a human.
That is the difference between a rewards programme and a faucet.
"""
from __future__ import annotations

from .base import Result, UNKNOWN, VERIFIED, REJECTED
from . import x_oembed, x_api, ig_graph, apify

__all__ = ["Result", "UNKNOWN", "VERIFIED", "REJECTED", "probe",
           "x_oembed", "x_api", "ig_graph", "apify"]


def probe() -> dict:
    """What can this box verify right now? Called by the CLI and /api/health so
    the answer is never a guess.

    Only the networks in config.SOCIAL_PLATFORMS are probed — a green light
    beside a network we do not run is noise an operator has to learn to ignore.
    """
    from .. import config
    out = {"platforms": list(config.SOCIAL_PLATFORMS)}
    if "x" in config.SOCIAL_PLATFORMS:
        out["x_oembed"] = x_oembed.probe()
        out["x_api"] = x_api.probe()
    if "instagram" in config.SOCIAL_PLATFORMS:
        out["ig_graph"] = ig_graph.probe()
    out["apify"] = apify.probe()
    return out
