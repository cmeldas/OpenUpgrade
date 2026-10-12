# Copyright Odoo Community Association (OCA)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from openupgradelib import openupgrade

from odoo import api, models

from odoo.addons.base.models.ir_model import (
    IrModel,
    IrModelData,
    IrModelFields,
    IrModelRelation,
)

_logger = logging.getLogger(__name__)


def _drop_table(self):
    """Never drop tables"""
    for model in self:
        if self.env.get(model.model) is not None:
            openupgrade.message(
                self.env.cr,
                "Unknown",
                False,
                False,
                "Not dropping the table or view of model %s",
                model.model,
            )


IrModel._drop_table = _drop_table


def _drop_column(self):
    """Never drop columns"""
    for field in self:
        if field.name in models.MAGIC_COLUMNS:
            continue
        openupgrade.message(
            self.env.cr,
            "Unknown",
            False,
            False,
            "Not dropping the column of field %s of model %s",
            field.name,
            field.model,
        )
        continue


IrModelFields._drop_column = _drop_column


def _module_data_uninstall(self):
    """Don't delete many2many relation tables. Only unlink the
    ir.model.relation record itself.
    """
    self.unlink()


IrModelRelation._module_data_uninstall = _module_data_uninstall


@api.model
def _process_end_unlink_record(self, record):
    """Don't break the migration on an obsolete record that cannot be deleted.

    The patch of BaseModel.unlink() only protects the base implementation.
    Overrides of unlink() run partly outside of it, and an exception raised
    there would abort the whole migration at its very end. Delete the record
    in a savepoint instead, and keep it (and its xmlid) if that fails.
    """
    try:
        with self.env.cr.savepoint():
            return _process_end_unlink_record._original_method(self, record)
    except Exception as e:
        _logger.warning(
            "Could not delete obsolete record %s, keeping it", record, exc_info=e
        )


_process_end_unlink_record._original_method = IrModelData._process_end_unlink_record
IrModelData._process_end_unlink_record = _process_end_unlink_record
