"""Portuguese (pt-BR) extension of the Music Assistant Alexa skill.

`install()` is called once, before the upstream web app builds its skill, by
`fork_ext.wsgi`. Nothing in the upstream source is edited.
"""

_installed = False


def _ensure_skill_access():
    """Ensure the allowed Alexa skill ID is registered even if deployment was not marked 'complete'."""
    import json
    import logging
    import private_access

    logger = logging.getLogger(__name__)

    # Fallback hook for private_access.get_allowed_skill_id
    orig_get_allowed = private_access.get_allowed_skill_id

    def _fork_get_allowed():
        allowed = orig_get_allowed()
        if allowed:
            return allowed
        try:
            dep_path = private_access.deployment_path()
            if dep_path.exists():
                state = json.loads(dep_path.read_text(encoding="utf-8"))
                skill_id = state.get("skill_id")
                if isinstance(skill_id, str) and skill_id.strip():
                    return skill_id.strip()
        except Exception:
            pass
        return None

    private_access.get_allowed_skill_id = _fork_get_allowed

    # Also persist to skill-access.json so upstream code sees it on disk
    try:
        current = _fork_get_allowed()
        access_p = private_access.access_path()
        if current and not access_p.exists():
            private_access.save_allowed_skill_id(current)
            logger.info("fork_ext: initialized skill-access restriction with skill ID: %s", current)
    except Exception as exc:
        logger.warning("fork_ext: failed to persist skill-access file: %s", exc)


def install():
    """Register the extension's handlers ahead of the upstream ones (idempotent)."""
    global _installed
    if _installed:
        return
    from . import handlers
    from . import upstream_api as upstream

    upstream.register_first(
        request_handlers=[handler() for handler in handlers.PRIORITY_HANDLERS],
        before="VoiceMusicHandler",
    )
    upstream.register_first(
        request_handlers=[handler() for handler in handlers.REQUEST_HANDLERS],
        exception_handlers=[handlers.ErrorHandler()],
        before="LaunchRequestOrPlayAudioHandler",
    )
    upstream.skill_builder.add_global_request_interceptor(handlers.PortugueseStrings())
    _ensure_skill_access()
    _installed = True

