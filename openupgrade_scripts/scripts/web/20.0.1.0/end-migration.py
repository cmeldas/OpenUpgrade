# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
import re

from psycopg2.extras import Json

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)

# `t-esc="..."` / `t-raw="..."` attributes (not `data-t-esc`, not `t-esc-...`)
T_ESC_ATTR_RE = re.compile(r"""(?<![\w-])t-(esc|raw)(?=\s*=\s*["'])""")
# `<attribute name="t-esc">` of inheritance specs
T_ESC_ATTRIBUTE_SPEC_RE = re.compile(
    r"""(<attribute\b[^>]*\bname\s*=\s*(["']))t-(esc|raw)(\2)"""
)
# `@t-esc` in the xpath expressions of inheritance specs
T_ESC_XPATH_RE = re.compile(r"@t-(esc|raw)\b")
# `active="..."` attribute of the root `<t t-name>` of a QWeb arch
ROOT_ACTIVE_RE = re.compile(r"""^(\s*<t\b[^>]*?)\s+active=(["'])[^"']*\2""")


def _convert_t_esc(arch, with_raw):
    """Return `arch` with the `t-esc` (and `t-raw` if `with_raw`) directives
    renamed to `t-out`."""
    directives = ("esc", "raw") if with_raw else ("esc",)

    def directive(match):
        return "t-out" if match.group(1) in directives else match.group(0)

    arch = T_ESC_ATTR_RE.sub(directive, arch)
    arch = T_ESC_ATTRIBUTE_SPEC_RE.sub(
        lambda m: m.group(1) + "t-out" + m.group(4)
        if m.group(3) in directives
        else m.group(0),
        arch,
    )
    arch = T_ESC_XPATH_RE.sub(
        lambda m: "@t-out" if m.group(1) in directives else m.group(0), arch
    )
    return arch


def _strip_root_active(arch):
    """19.0 kept the `active` attribute of `<template active="...">` on the
    root of the arch; 20.0 does not, and `ir.qweb` warns about it as an unused
    attribute each time the template is compiled."""
    if "t-name=" not in arch[: arch.find(">") + 1]:
        return arch
    return ROOT_ACTIVE_RE.sub(r"\1", arch, count=1)


def views_qweb_19_directives(env):
    """Odoo 20.0 removed the `t-esc` and `t-raw` directives, from the server
    QWeb engine (`ir.qweb` only logs "Unknown directives" and outputs nothing)
    and from Owl 3. In 19.0 both were mere aliases of `t-out` (`t-raw` only
    logged a deprecation warning), so renaming them keeps the 19.0 output.

    Module views are reloaded from the 20.0 files; this converts the views
    kept from 19.0: website-specific copies, views without updatable XML-ID
    (website pages, custom views) and noupdate views (e.g. mail layouts such
    as `digest.digest_mail_main`, `portal.portal_share_template`), in every
    language of `arch_db`. Xpath expressions and `<attribute name="t-esc">`
    of inheriting specs are renamed as well, the 20.0 parents use `t-out`.
    `t-raw` is only renamed in QWeb views (in Owl templates it was not an
    alias of `t-out`). The `active` attribute 19.0 left on the root of some
    QWeb archs (unused, logged as unknown directive by 20.0) is dropped.
    """
    cr = env.cr
    cr.execute(
        r"""
        SELECT v.id, v.key, v.type, v.arch_db
        FROM ir_ui_view v
        WHERE (
                v.arch_db::text ~ 't-(esc|raw)\M'
                OR (v.type = 'qweb' AND v.arch_db::text ~ '<t [^>]*active=')
            )
            AND (
                v.website_id IS NOT NULL
                OR NOT EXISTS (
                    SELECT 1 FROM ir_model_data d
                    WHERE d.model = 'ir.ui.view' AND d.res_id = v.id
                        AND NOT COALESCE(d.noupdate, FALSE)
                )
            )
        ORDER BY v.id
        """
    )
    for view_id, key, view_type, arch_db in cr.fetchall():
        new_arch_db = {}
        for lang, arch in arch_db.items():
            if arch:
                arch = _convert_t_esc(arch, view_type == "qweb")
                if view_type == "qweb":
                    arch = _strip_root_active(arch)
            new_arch_db[lang] = arch
        if new_arch_db != arch_db:
            _logger.info("View %s (%s): 19.0 QWeb directives converted", view_id, key)
            cr.execute(
                "UPDATE ir_ui_view SET arch_db = %s WHERE id = %s",
                (Json(new_arch_db), view_id),
            )
    env["ir.ui.view"].invalidate_model(["arch_db"])


@openupgrade.migrate()
def migrate(env, version):
    views_qweb_19_directives(env)
