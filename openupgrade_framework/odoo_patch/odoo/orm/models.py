# Copyright Odoo Community Association (OCA)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import functools
import logging
from contextlib import contextmanager

from odoo.models import BaseModel

_logger = logging.getLogger(__name__)

# Odoo 20 only keeps MODULE_UNINSTALL_FLAG for backward compatibility and checks
# the 'force_delete' context key explicitly. Odoo sets it when it deletes
# obsolete records in ir.model.data's _process_end(), when it removes obsolete
# fields and selection values of updated models and when it uninstalls modules.
FORCE_DELETE = "force_delete"

# Methods decorated with @api.ondelete(at_uninstall=False) that do not check
# whether a deletion is allowed but delete data linked to the deleted records
# instead. During a migration, the data still belongs to the database: the
# migration scripts may not have moved it yet or the database administrator may
# want to recover it from the legacy tables. These methods are not called on
# forced deletions (like obsolete models removed by _process_end()).
KEEP_DATA_ONDELETE_METHODS = {
    # base: attachments and files of removed models
    ("ir.model", "_unlink_related_attachments"),
    # mail: messages, followers, activities and activity types of removed models
    # (activities are deleted anyway by the ON DELETE CASCADE foreign key of
    # mail_activity.res_model_id)
    ("ir.model", "_unlink_related_mail_data"),
}

_MISSING = object()


def _lenient_ondelete_method(func, model_name):
    """Wrap an @api.ondelete(at_uninstall=False) method for forced deletions.

    Up to Odoo 19, these methods were skipped altogether when deleting records
    with MODULE_UNINSTALL_FLAG in the context. As of Odoo 20, they are only
    skipped for the methods of the modules that are being uninstalled
    (registry.uninstalling_modules), so they also run on the obsolete records
    that _process_end() deletes during the migration. Run them, so that the
    cleanups among them still happen, but don't let a failing check prevent
    the deletion of an obsolete record.
    """
    if (model_name, func.__name__) in KEEP_DATA_ONDELETE_METHODS:

        def wrapper(records):
            _logger.info(
                "Not calling %s on forced deletion of %s %s: keeping their linked data",
                func.__name__,
                records._name,
                records.mapped("model") if records._name == "ir.model" else records.ids,
            )

    else:

        def wrapper(records):
            try:
                with records.env.cr.savepoint():
                    func(records)
            except Exception as e:
                _logger.warning(
                    "Ignoring failed check %s on forced deletion of %s with ids %s",
                    func.__name__,
                    records._name,
                    records.ids,
                    exc_info=e,
                )

    # keep __name__, __module__ (read by unlink() to determine the module of
    # the method) and the _ondelete attribute set by the decorator
    functools.update_wrapper(wrapper, func)
    wrapper._openupgrade_lenient = True
    return wrapper


@contextmanager
def _lenient_ondelete_methods(records):
    """Temporarily replace the ondelete methods of the records' model by their
    lenient versions (see _lenient_ondelete_method).

    BaseModel.unlink() reads the methods from the class attribute
    _ondelete_methods, which BaseModel memoizes on the model's class.
    """
    cls = type(records)
    methods = records._ondelete_methods  # memoizes them on the class
    previous = cls.__dict__.get("_ondelete_methods", _MISSING)
    lenient = [
        func
        if func._ondelete or getattr(func, "_openupgrade_lenient", False)
        else _lenient_ondelete_method(func, records._name)
        for func in methods
    ]
    cls._ondelete_methods = lenient
    try:
        yield
    finally:
        # A registry setup during the unlink (ir.model's unlink() does one)
        # resets the attribute: then leave the new value alone.
        if cls.__dict__.get("_ondelete_methods") is lenient:
            if previous is _MISSING:
                del cls._ondelete_methods
            else:
                cls._ondelete_methods = previous


def unlink(self):
    """Don't break on unlink of obsolete records
    when called from ir.model.data::_process_end()

    The deletion happens in a savepoint. If it fails, the savepoint is rolled
    back, together with the state of the ORM transaction, and a warning is
    logged. Checks implemented with @api.ondelete(at_uninstall=False) don't
    prevent the deletion, and the ondelete methods that would remove data
    linked to obsolete models are not called (see KEEP_DATA_ONDELETE_METHODS).

    This only adapts the base unlink method. If overrides of this method
    on individual models give problems, add patches for those as well.
    """
    if not self.env.context.get(FORCE_DELETE):
        return BaseModel.unlink._original_method(self)
    if not self:
        return True
    try:
        with self.env.cr.savepoint(), _lenient_ondelete_methods(self):
            return BaseModel.unlink._original_method(self)
    except Exception as e:
        _logger.warning(
            "Could not delete obsolete record with ids %s of model %s",
            self.ids,
            self._name,
            exc_info=e,
        )
    return False


unlink._original_method = BaseModel.unlink
BaseModel.unlink = unlink
