# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Render the historical tracking values into the message bodies.

Up to 19.0 the chatter displayed the mail.tracking.value records of a message
(formatted on the fly). In 20.0 tracking values are rendered once into
mail.message.body with the template mail.mail_tracking_template when the
message is created (mail.thread._message_compute_body_with_trackings) and the
model mail.tracking.value only survives in the optional module mail_tracking.
Without this script the history of every record shows empty notifications.

This runs as end-migration because the labels, sequences and value formats
come from the models of all modules (stock.picking, mrp.production, ...),
which are only all loaded at the end. The values are formatted with the very
methods Odoo 20 uses at message creation
(mail.track.mixin._create_mail_tracking_values), in the language and timezone
of the message author.
"""

import json
import logging
from collections import defaultdict

from openupgradelib import openupgrade
from psycopg2.extras import execute_values

from odoo.tools import split_every

_logger = logging.getLogger(__name__)

TRACKING_FIELD_TABLE = "openupgrade_legacy_20_0_mail_tracking_field"
# previous body and type of every message rendered here (audit + idempotence)
RENDERED_TABLE = "openupgrade_legacy_20_0_mail_message_tracking"

# Material Symbols names used by Odoo 20 for the activity types that used
# FontAwesome icons up to 19.0 (taken from the data files of both versions)
ACTIVITY_ICONS = {
    "fa-car": "directions_car",
    "fa-check": "check",
    "fa-dollar": "attach_money",
    "fa-envelope": "mail",
    "fa-phone": "phone",
    "fa-sun-o": "light_mode",
    "fa-tasks": "checklist",
    "fa-upload": "upload",
    "fa-users": "group",
    "fa-warning": "warning",
    "fa-wrench": "build",
}

_VALUE_COLUMNS = {
    "integer": "integer",
    "float": "float",
    "monetary": "float",
    "char": "char",
    "text": "text",
    "datetime": "datetime",
    "date": "datetime",
    "boolean": "integer",
    "selection": "char",
    "many2one": "char",
    "one2many": "char",
    "many2many": "char",
    "tags": "char",
}


def _load_tracking_fields(env):
    """Field definitions of the tracking values: the current ir.model.fields
    row when it still exists as a field of the registry with the same type
    (also covers fields renamed by migration scripts, e.g. res.partner
    peppol_eas -> routing_scheme), else the 19.0 definition snapshotted by the
    pre-migration (removed fields and models). The value columns were filled
    according to the 19.0 type, so a field whose type changed keeps the
    snapshot."""
    cr = env.cr
    fields_by_id = {}
    if openupgrade.table_exists(cr, TRACKING_FIELD_TABLE):
        cr.execute(
            "SELECT id, model, name, ttype, field_description "
            f"FROM {TRACKING_FIELD_TABLE}"
        )
        for fid, model, name, ttype, description in cr.fetchall():
            fields_by_id[fid] = (model, name, ttype, description or {})
    cr.execute(
        """
        SELECT id, model, name, ttype, field_description
        FROM ir_model_fields
        WHERE id IN (
            SELECT DISTINCT field_id FROM mail_tracking_value
            WHERE field_id IS NOT NULL
        )
        """
    )
    for fid, model, name, ttype, description in cr.fetchall():
        snapshot = fields_by_id.get(fid)
        if not snapshot or (
            snapshot[2] == ttype and _field_in_registry(env, model, name)
        ):
            fields_by_id[fid] = (model, name, ttype, description or {})
    return fields_by_id


def _field_in_registry(env, model, name):
    return bool(model and model in env and name in env[model]._fields)


def _fill_removed_field_info(env, fields_by_id, field_column):
    """Fill field_info (desc, name, sequence, type) of tracking values whose
    field does not exist in 20.0, as mail_tracking's ir.model.fields
    _unlink_tracking hook does for removed fields. That hook skips fields of
    removed models (e.g. stock.scrap), whose field_id is then set to NULL and
    the tracking would lose its label. Values whose ir.model.fields row is
    already gone (field_id set to NULL by an earlier script) are filled too,
    whatever the registry says."""
    cr = env.cr
    unlinked = set()
    if field_column != "field_id":
        cr.execute(
            f"""
            SELECT DISTINCT {field_column} FROM mail_tracking_value
            WHERE field_id IS NULL AND {field_column} IS NOT NULL
            """
        )
        unlinked = {fid for (fid,) in cr.fetchall()}
    gone = [
        (fid, model, name, ttype, description)
        for fid, (model, name, ttype, description) in fields_by_id.items()
        if fid in unlinked or not _field_in_registry(env, model, name)
    ]
    for fid, _model, name, ttype, description in gone:
        openupgrade.logged_query(
            cr,
            f"""
            UPDATE mail_tracking_value
            SET field_info = COALESCE(field_info, '{{}}'::jsonb) || %s::jsonb
            WHERE {field_column} = %s
                AND NOT COALESCE(field_info, '{{}}'::jsonb) ? 'name'
            """,
            (
                json.dumps(
                    {
                        "desc": description.get("en_US") or name,
                        "name": name,
                        "sequence": 100,
                        "type": ttype,
                    }
                ),
                fid,
            ),
        )


def _languages(env):
    installed = [code for code, _name in env["res.lang"].get_installed()]
    admin = env.ref("base.user_admin", raise_if_not_found=False)
    default_lang = (
        admin.lang if admin and admin.lang in installed else (installed or ["en_US"])[0]
    )
    default_tz = (admin and admin.tz) or "UTC"
    return set(installed), default_lang, default_tz


class _Renderer:
    """Builds the tracking values of one message the way Odoo 20 does when the
    message is created, in a given language and timezone."""

    def __init__(self, env, lang, tz, fields_by_id):
        self.env = env(context=dict(env.context, lang=lang, tz=tz))
        self.thread = self.env["mail.thread"]
        self.fields_by_id = fields_by_id
        self.col_info_cache = {}
        self.sequence_cache = {}

    def _col_info(self, model, name, ttype, description, field_info):
        key = (model, name, ttype)
        if key in self.col_info_cache and ttype != "properties":
            return dict(self.col_info_cache[key])
        lang = self.env.lang
        info = {"type": ttype}
        if ttype != "properties" and _field_in_registry(self.env, model, name):
            field_get = self.env[model].fields_get(
                [name], attributes=("string", "company_dependent")
            )[name]
            info["string"] = field_get["string"]
            info["company_dependent"] = field_get.get("company_dependent")
        elif field_info.get("desc") and (ttype == "properties" or not description):
            info["string"] = field_info["desc"]
        else:
            info["string"] = (
                description.get(lang)
                or description.get("en_US")
                or field_info.get("desc")
                or name
                or "Unknown"
            )
        if ttype != "properties":
            self.col_info_cache[key] = dict(info)
        return info

    def _sequence(self, model, name, field_info):
        key = (model, name)
        if key not in self.sequence_cache:
            sequence = field_info.get("sequence", 100)
            if _field_in_registry(self.env, model, name):
                Model = self.env[model]
                if hasattr(Model, "_mail_track_get_field_sequence"):
                    sequence = Model._mail_track_get_field_sequence(name)
                else:
                    sequence = getattr(Model._fields[name], "tracking", True)
                    sequence = 100 if sequence in (True, None, False) else sequence
            self.sequence_cache[key] = sequence
        return self.sequence_cache[key]

    @staticmethod
    def _values(value_type, row):
        column = _VALUE_COLUMNS.get(value_type, "char")
        old, new = row[f"old_value_{column}"], row[f"new_value_{column}"]
        if value_type == "date":
            return (old.date() if old else False), (new.date() if new else False)
        if value_type == "boolean":
            return bool(old), bool(new)
        if value_type == "many2one":
            return (
                (row["old_value_integer"] or 0, old or ""),
                (row["new_value_integer"] or 0, new or ""),
            )
        if value_type in ("one2many", "many2many", "tags"):
            return ([(0, old)] if old else False), ([(0, new)] if new else False)
        if value_type in ("selection", "char", "text"):
            return old or "", new or ""
        if value_type in ("integer", "float", "monetary"):
            return old or 0, new or 0
        return old or False, new or False

    def tracking_values(self, rows):
        """Return the tracking values of one message, ordered like
        mail.track.mixin._mail_track (sequence, properties, name; descending),
        which is the order _message_compute_body_with_trackings expects."""
        prepared = []
        for row in rows:
            field_info = row["field_info"] or {}
            field = self.fields_by_id.get(row["fid"])
            if field:
                model, name, ttype, description = field
            else:
                model, name, ttype, description = (
                    None,
                    field_info.get("name"),
                    field_info.get("type") or "char",
                    {},
                )
            value_type = (
                field_info.get("type") or "char" if ttype == "properties" else ttype
            )
            col_info = self._col_info(model, name, ttype, description, field_info)
            col_info["type"] = value_type
            if value_type == "monetary":
                currency_id = row.get("currency_id") or field_info.get("currency_id")
                if currency_id:
                    col_info["currency_id"] = currency_id
                else:
                    col_info["type"] = value_type = "float"
            if value_type not in _VALUE_COLUMNS:
                col_info["type"] = value_type = "char"
            old, new = self._values(value_type, row)
            if value_type == "selection":
                col_info["selection"] = [(v, v) for v in {old, new} if v]
            values = self.thread._create_mail_tracking_values(
                old, new, name or "unknown", col_info
            )
            prepared.append(
                (
                    (
                        self._sequence(model, name, field_info),
                        ttype == "properties",
                        name or "",
                        row["id"],
                    ),
                    values,
                )
            )
        prepared.sort(key=lambda item: item[0], reverse=True)
        return [values for _key, values in prepared]

    def body(self, body, rows):
        return self.thread._message_compute_body_with_trackings(
            body or "", self.tracking_values(rows)
        )


def _render_tracking_bodies(env):
    cr = env.cr
    legacy_column = openupgrade.get_legacy_name("field_id")
    if openupgrade.column_exists(cr, "mail_tracking_value", legacy_column):
        field_column = f"COALESCE(v.{legacy_column}, v.field_id)"
        field_column_update = f"COALESCE({legacy_column}, field_id)"
    else:
        field_column = field_column_update = "field_id"
    currency_column = (
        "v.currency_id"
        if openupgrade.column_exists(cr, "mail_tracking_value", "currency_id")
        else "NULL::integer"
    )
    fields_by_id = _load_tracking_fields(env)
    _fill_removed_field_info(env, fields_by_id, field_column_update)

    if not openupgrade.table_exists(cr, RENDERED_TABLE):
        openupgrade.logged_query(
            cr,
            f"""
            CREATE TABLE {RENDERED_TABLE} (
                id integer PRIMARY KEY,
                body text,
                message_type varchar
            )
            """,
        )
    installed, default_lang, default_tz = _languages(env)
    cr.execute(
        f"""
        SELECT m.id, m.body, m.message_type, p.lang, p.tz
        FROM mail_message m
        LEFT JOIN res_partner p ON p.id = m.author_id
        WHERE m.message_type != 'tracking'
            AND EXISTS (
                SELECT 1 FROM mail_tracking_value v WHERE v.mail_message_id = m.id
            )
            AND NOT EXISTS (SELECT 1 FROM {RENDERED_TABLE} r WHERE r.id = m.id)
        ORDER BY m.id
        """
    )
    messages = cr.fetchall()
    _logger.info("Rendering tracking values into %s message bodies", len(messages))
    renderers = {}
    done = 0
    # mail.message.create sanitizes the body (e.g. <br/> becomes <br>)
    Message = env["mail.message"]
    body_field = Message._fields["body"]
    for chunk in split_every(1000, messages):
        ids = tuple(message[0] for message in chunk)
        cr.execute(
            f"""
            SELECT v.id, v.mail_message_id, {field_column} AS fid, v.field_info,
                v.old_value_integer, v.new_value_integer,
                v.old_value_float, v.new_value_float,
                v.old_value_char, v.new_value_char,
                v.old_value_text, v.new_value_text,
                v.old_value_datetime, v.new_value_datetime,
                {currency_column} AS currency_id
            FROM mail_tracking_value v
            WHERE v.mail_message_id IN %s
            ORDER BY v.mail_message_id, v.id
            """,
            (ids,),
        )
        columns = [description[0] for description in cr.description]
        rows_by_message = defaultdict(list)
        for values in cr.fetchall():
            row = dict(zip(columns, values, strict=True))
            rows_by_message[row["mail_message_id"]].append(row)
        updates = []
        for message_id, body, message_type, lang, tz in chunk:
            lang = lang if lang in installed else default_lang
            tz = tz or default_tz
            renderer = renderers.get((lang, tz))
            if renderer is None:
                renderer = renderers[(lang, tz)] = _Renderer(
                    env, lang, tz, fields_by_id
                )
            new_body = renderer.body(body, rows_by_message[message_id])
            new_body = body_field.convert_to_column(new_body, Message)
            updates.append(
                (
                    message_id,
                    str(new_body),
                    "tracking" if message_type == "notification" else message_type,
                )
            )
        execute_values(
            cr,
            f"""
            INSERT INTO {RENDERED_TABLE} (id, body, message_type)
            SELECT m.id, m.body, m.message_type
            FROM mail_message m
            JOIN (VALUES %s) AS data(id) ON data.id = m.id
            """,
            [(message_id,) for message_id, _body, _type in updates],
        )
        execute_values(
            cr,
            """
            UPDATE mail_message m
            SET body = data.body, message_type = data.message_type
            FROM (VALUES %s) AS data(id, body, message_type)
            WHERE m.id = data.id
            """,
            updates,
        )
        done += len(updates)
        _logger.info("Rendered tracking values of %s/%s messages", done, len(messages))
    env["mail.message"].invalidate_model(["body", "message_type"])


def _activity_type_icons(cr):
    """Activity type icons are Material Symbols in 20.0 (rendered through
    data-icon), FontAwesome classes from 19.0 show nothing. Records of the
    modules are updated by their noupdate changes, this converts the others."""
    for old, new in ACTIVITY_ICONS.items():
        openupgrade.logged_query(
            cr,
            "UPDATE mail_activity_type SET icon = %s WHERE icon = %s",
            (new, old),
        )
    cr.execute("SELECT id, icon FROM mail_activity_type WHERE icon LIKE 'fa-%%'")
    for type_id, icon in cr.fetchall():
        _logger.warning(
            "Activity type %s keeps the FontAwesome icon %s, set a Material "
            "Symbols icon manually",
            type_id,
            icon,
        )


@openupgrade.migrate()
def migrate(env, version):
    _render_tracking_bodies(env)
    _activity_type_icons(env.cr)
