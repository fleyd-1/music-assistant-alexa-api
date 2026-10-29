# -*- coding: utf-8 -*-
import gettext

_ = gettext.gettext

import os
import re
import sys

# Fuege /app/src/ zum Python-Pfad hinzu
_app_src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _app_src not in sys.path:
    sys.path.insert(0, _app_src)

WELCOME_MSG = _("")
HELP_MSG = _("Bem-vindo ao Music Assistant. Podes pedir músicas, artistas, rádios, álbuns, playlists ou podcasts, os teus favoritos ou a última rádio. Também podes perguntar o que está a tocar ou pedir para parar a música daqui a trinta minutos. O que queres ouvir?")
UNHANDLED_MSG = _("Não percebi o que disseste. Podes repetir dizendo o que queres ouvir?")
CANNOT_SKIP_MSG = _("Em transmissões de rádio não é possível saltar de faixa.")
RESUME_MSG = _("A retomar {}")
NOT_POSSIBLE_MSG = _("Esse comando não está disponível para esta reprodução.")
STOP_MSG = _("")
DEVICE_NOT_SUPPORTED = _("Este dispositivo não suporta reprodução de áudio.")
DEVICE_NOT_MAPPED_MSG = _("Esta coluna ainda não está associada a um leitor do Music Assistant. Configura-a na página de dispositivos.")
MA_COMMAND_FAILED_MSG = _("Não foi possível comunicar com o Music Assistant para executar o comando.")

from contextvars import ContextVar

import shared_store

_request_info = ContextVar('playback_metadata', default=None)


def _metadata(payload):
    payload = payload or {}
    secondary = ' - '.join(str(value) for value in
                           (payload.get('artist'), payload.get('album')) if value)
    image = payload.get('imageUrl') or ''
    url = re.sub(r'(?i)\.flac(?=$|\?)', '.mp3', payload.get('streamUrl') or '')
    return {
        'audioSources': url, 'backgroundImageSource': image,
        'coverImageSource': image, 'headerAttributionImage': '',
        'headerTitle': '', 'headerSubtitle': '',
        'primaryText': payload.get('title') or '', 'secondaryText': secondary,
        'timestamp': payload.get('timestamp', 0),
    }


def begin_request(player_id=None):
    _request_info.set(_metadata(shared_store.get_ma(player_id)))


def get_info():
    snapshot = _request_info.get()
    return dict(snapshot) if snapshot is not None else _metadata(shared_store.get_ma())


def get_latest(*args, **kwargs):
    """Read this process directly; an empty store is normal before the first push."""
    begin_request()
    return {'changed': bool(get_info().get('audioSources'))}
