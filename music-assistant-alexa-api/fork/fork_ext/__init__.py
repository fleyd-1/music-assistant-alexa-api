"""Portuguese (pt-BR) extension of the Music Assistant Alexa skill.

`install()` is called once, before the upstream web app builds its skill, by
`fork_ext.wsgi`. Nothing in the upstream source is edited.
"""

_installed = False


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
    _installed = True
