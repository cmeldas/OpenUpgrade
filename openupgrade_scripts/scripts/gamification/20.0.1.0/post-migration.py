# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)

# Czech wording of the 20.0 body of email_template_badge_received, applied
# term by term on the stored English body. The 20.0 cs.po has no msgstr for
# this body, and the 18.0 -> 19.0 migration already dropped the Czech body
# (delete_record_translations with an empty msgstr in the 19.0 cs.po).
_BADGE_RECEIVED_CS_CZ = [
    ("<div>Hi <strong>", "<div>Dobrý den, <strong>"),
    ("You've just been awarded the <strong>", "právě jste získali odznak <strong>"),
    ("</strong> badge", "</strong>"),
    (" by <strong>", " od uživatele <strong>"),
    (" says:</strong>", " píše:</strong>"),
    (
        "Thank you for your contributions, and keep shining!",
        "Děkujeme za váš přínos a jen tak dál!",
    ),
]


def _badge_received_czech_body(env):
    """Give back a Czech body to the badge notification when Czech is
    installed and the template has none."""
    cr = env.cr
    cr.execute("SELECT 1 FROM res_lang WHERE code = 'cs_CZ' AND active")
    if not cr.fetchone():
        return
    cr.execute(
        """
        SELECT template.id, template.body_html->>'en_US'
        FROM mail_template template
        JOIN ir_model_data imd
            ON imd.model = 'mail.template' AND imd.res_id = template.id
        WHERE imd.module = 'gamification'
            AND imd.name = 'email_template_badge_received'
            AND template.body_html IS NOT NULL
            AND NOT template.body_html ? 'cs_CZ'
        """
    )
    row = cr.fetchone()
    if not row or not row[1]:
        return
    template_id, body = row
    for english, czech in _BADGE_RECEIVED_CS_CZ:
        if body.count(english) != 1:
            _logger.warning(
                "Body of gamification.email_template_badge_received does not "
                "match the 20.0 one (%r), Czech body not restored",
                english,
            )
            return
        body = body.replace(english, czech)
    openupgrade.logged_query(
        cr,
        """
        UPDATE mail_template
        SET body_html = body_html || jsonb_build_object('cs_CZ', %s::text)
        WHERE id = %s
        """,
        (body, template_id),
    )


@openupgrade.migrate()
def migrate(env, version):
    # noupdate templates changed in 20.0: email layouts on all of them, new
    # body of the badge notification (the cs.po of 20.0 has no translation of
    # it, the bodies of the others did not change and keep their translations)
    openupgrade.load_data(env, "gamification", "20.0.1.0/noupdate_changes.xml")
    _badge_received_czech_body(env)
