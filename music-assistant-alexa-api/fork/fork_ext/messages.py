"""Everything the skill says aloud in Portuguese, in one place."""

ASK_WHAT_TO_PLAY = "Diz-me o que queres ouvir."
NOT_UNDERSTOOD = "Não percebi. Diz-me o que queres ouvir."
UNHANDLED = "Não percebi o que disseste. Podes repetir dizendo o que queres ouvir?"
ERROR_ASK_AGAIN = "Ocorreu um erro no Music Assistant. Diz-me o que queres ouvir."
NOT_MAPPED = "Esta coluna ainda não está associada a um leitor do Music Assistant."
HELP = (
    "Bem-vindo ao Music Assistant. Podes pedir músicas, artistas, rádios, álbuns, "
    "playlists ou podcasts, os teus favoritos ou a última rádio. Também podes "
    "perguntar o que está a tocar ou pedir para parar a música daqui a trinta "
    "minutos. O que queres ouvir?"
)
PLAYING = "A tocar {}."

SEARCH_ERRORS = {
    "not_found": "Não encontrei isso no Music Assistant.",
    "missing_api": "A pesquisa por voz ainda não está configurada no Music Assistant.",
    "stream_timeout": "O Music Assistant não enviou o áudio a tempo.",
    "timeout": "O Music Assistant demorou demasiado a preparar o áudio.",
    "api_error": "Não consegui pesquisar no Music Assistant. Verifica a ligação da API.",
}

JOANA_ERRORS = {
    "not_found": "Não encontrei o podcast no Music Assistant.",
    "player_unavailable": "Esta coluna não está disponível no Music Assistant.",
    "missing_api": "A ligação ao Music Assistant não está configurada.",
    "stream_timeout": "O Music Assistant não enviou o episódio a tempo. Tenta novamente.",
    "timeout": "O Music Assistant demorou demasiado a preparar o episódio. Tenta novamente.",
    "api_error": "Não consegui iniciar o episódio no Music Assistant.",
}

SHORTCUT_TITLES = {
    "favorites": "os teus favoritos",
    "last_radio": "a última rádio",
    "recent": "o que estavas a ouvir",
}

SHORTCUT_ERRORS = {
    "favorites": "Não encontrei músicas favoritas no Music Assistant.",
    "last_radio": "Não encontrei nenhuma rádio ouvida recentemente.",
    "recent": "Não encontrei nada ouvido recentemente.",
}

SHORTCUT_COMMON_ERRORS = {
    "missing_api": "A ligação ao Music Assistant não está configurada.",
    "stream_timeout": "O Music Assistant não enviou o áudio a tempo.",
    "timeout": "O Music Assistant demorou demasiado a preparar o áudio.",
    "api_error": "Não consegui falar com o Music Assistant.",
}

NOW_PLAYING_BOTH = "Está a tocar {title}, de {artist}."
NOW_PLAYING_TITLE = "Está a tocar {title}."
NOW_PLAYING_NONE = "Não consegui saber o que está a tocar."

SLEEP_ASK = "Daqui a quanto tempo queres que a música pare? Por exemplo, trinta minutos."
SLEEP_SET = "Combinado. Paro a música daqui a {}."
SLEEP_CANCELLED = "Cancelei. A música continua a tocar."
SLEEP_NONE = "Não havia nenhum temporizador ligado."

SHUFFLE_ON = "Modo aleatório ativado."
SHUFFLE_OFF = "Modo aleatório desativado."
REPEAT_ON = "Repetição ativada."
REPEAT_OFF = "Repetição desativada."
QUEUE_CHANGE_FAILED = "Não foi possível alterar a reprodução no Music Assistant."
DONE = "Comando executado."

# Replacements for upstream's translated messages, keyed by the name of the
# constant in the upstream skill's `data` module (see handlers.PortugueseStrings).
UPSTREAM_OVERRIDES = {
    "UNHANDLED_MSG": "Não percebi o que disseste. Podes repetir dizendo o que queres ouvir?",
    "CANNOT_SKIP_MSG": "Em transmissões de rádio não é possível saltar de faixa.",
    "RESUME_MSG": "A retomar {}",
    "NOT_POSSIBLE_MSG": "Esse comando não está disponível para esta reprodução.",
    "DEVICE_NOT_SUPPORTED": "Este dispositivo não suporta reprodução de áudio.",
    "DEVICE_NOT_MAPPED_MSG": (
        "Esta coluna ainda não está associada a um leitor do Music Assistant. "
        "Configura-a na página de dispositivos."),
    "MA_COMMAND_FAILED_MSG": "Não foi possível comunicar com o Music Assistant para executar o comando.",
}
