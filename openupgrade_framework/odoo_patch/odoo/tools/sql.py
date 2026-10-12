# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import logging

import psycopg2
from openupgradelib import openupgrade

import odoo.tools.sql

_logger = logging.getLogger(__name__)


def _convert_column(cr, tablename, columnname, columntype, using):
    """Never crash on a column type conversion that the database refuses.

    Odoo 20 converts the column of a field whose type changed with
    ``ALTER COLUMN ... TYPE ... USING column::type`` and the upgrade crashes
    when PostgreSQL cannot cast the data (there is no fallback that moves the
    old column away). Migration scripts are supposed to convert such columns in their
    pre-migration step. If they missed one, keep its data in a legacy column,
    create an empty column of the new type and log an error, so that the
    missing conversion can be spotted and written without losing the data.
    """
    try:
        with cr.savepoint(flush=False):
            return _convert_column._original_method(
                cr, tablename, columnname, columntype, using
            )
    except psycopg2.Error as e:
        legacy_name = openupgrade.get_legacy_name(columnname)
        suffix = 0
        while openupgrade.column_exists(cr, tablename, legacy_name):
            suffix += 1
            legacy_name = f"{openupgrade.get_legacy_name(columnname)}_{suffix}"
        _logger.error(
            "Could not convert column %s of table %s to type %s (%s). Its data is "
            "kept in column %s and the new column stays empty (no default or "
            "compute is applied): write a migration script to convert it.",
            columnname,
            tablename,
            columntype,
            str(e).strip(),
            legacy_name,
        )
        openupgrade.rename_columns(cr, {tablename: [(columnname, legacy_name)]})
        # The ORM does not know the legacy column: a NOT NULL constraint left on
        # it would make every later insert into the table fail. (Odoo only drops
        # such constraints in _check_removed_columns(), which already ran.)
        odoo.tools.sql.drop_not_null(cr, tablename, legacy_name)
        odoo.tools.sql.create_column(cr, tablename, columnname, columntype)


_convert_column._original_method = odoo.tools.sql._convert_column
odoo.tools.sql._convert_column = _convert_column
