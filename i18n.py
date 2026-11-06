"""Texts students see from the bot. German by default, English with the International role."""

import discord

DEFAULT_LANGUAGE = "de"

STRINGS = {
    "de": {
        "menu_server_only": "Dieses Menü funktioniert nur auf einem Server.",
        "role_gone": "Diese Rolle gibt es nicht mehr.",
        "role_not_assignable": "Diese Rolle kann nicht vergeben werden.",
        "role_too_high": "Ich kann diese Rolle nicht verwalten, weil sie über meiner höchsten Rolle steht.",
        "role_removed": "Die Rolle **{role}** wurde entfernt.",
        "role_added": "Du hast jetzt die Rolle **{role}**.",
        "role_full": "Die Rolle **{role}** ist auf {limit} Mitglieder begrenzt und gerade voll.",
        "no_permission": "Mir fehlt die Berechtigung, diese Rolle zu ändern.",
        "try_again": "Etwas ist schiefgelaufen, bitte versuche es noch einmal.",
        "rules_accepted": "Danke! Der Server ist jetzt freigeschaltet. Wähle als Nächstes in #studiengangwahl deinen Studiengang.",
        "rules_already_accepted": "Du hast die Regeln bereits akzeptiert.",
        "temp_voice_name": "Raum von {name}",
    },
    "en": {
        "menu_server_only": "This menu only works inside a server.",
        "role_gone": "That role no longer exists.",
        "role_not_assignable": "That role can't be given out.",
        "role_too_high": "I can't manage that role because it is above my highest role.",
        "role_removed": "Removed the **{role}** role.",
        "role_added": "You now have the **{role}** role.",
        "role_full": "The **{role}** role is limited to {limit} member(s) and is full right now.",
        "no_permission": "I don't have permission to change that role.",
        "try_again": "Something went wrong, please try again.",
        "rules_accepted": "Thanks! The server is now unlocked. Next, go to #studiengangwahl and pick your degree programme.",
        "rules_already_accepted": "You have already accepted the rules.",
        "temp_voice_name": "{name}'s room",
    },
}


def member_language(guild_config: dict, member: discord.Member) -> str:
    role_id = guild_config.get("international_role_id")
    if role_id and member.get_role(int(role_id)) is not None:
        return "en"
    return DEFAULT_LANGUAGE


def tr(language: str, key: str, **values) -> str:
    return STRINGS[language][key].format(**values)
