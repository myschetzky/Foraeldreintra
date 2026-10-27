"""Fejltyper. Opdelt efter hvad integrationen skal gøre ved dem."""

from __future__ import annotations


class SkoleIntraError(Exception):
    """Basisfejl."""


class AuthError(SkoleIntraError):
    """Forkert brugernavn/adgangskode -> Home Assistant starter re-autentificering."""


class ManualActionRequired(SkoleIntraError):
    """Skolen kræver noget, vi ikke må/kan gøre automatisk (MitID, bekræft kontaktoplysninger)."""


class TransientError(SkoleIntraError):
    """Timeout, netværk, 5xx eller vedligehold — prøv igen senere med backoff."""


class BlockedError(TransientError):
    """Skolens automatiseringsbeskyttelse har afvist os — vent længe før næste forsøg."""


class LayoutError(SkoleIntraError):
    """Siden blev hentet, men det forventede indhold blev ikke fundet (layout ændret?)."""
