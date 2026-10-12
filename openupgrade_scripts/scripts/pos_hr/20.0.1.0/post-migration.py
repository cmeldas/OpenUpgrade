# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade

# v19 access level -> v20 role, in the order in which v19 resolved the role of
# an employee listed in several levels (hr.employee._load_pos_data_read:
# advanced -> manager, else minimal -> minimal, else cashier). The v20 roles
# are exclusive, so an employee listed twice in v19 keeps the role v19 gave.
_role_mapping = [
    # "Employees with manager access"
    ("pos_hr_advanced_employee_hr_employee", "pos_hr_manager_employee_hr_employee"),
    # "Employees with minimal access": no refund / negative quantity
    (
        "pos_hr_minimal_employee_hr_employee",
        "pos_hr_restrictive_employee_hr_employee",
    ),
    # "Employees with basic access": full register access
    ("pos_hr_basic_employee_hr_employee", "pos_hr_cashier_employee_hr_employee"),
]


def _pos_config_employee_roles(env):
    """v19 minimal / basic / advanced_employee_ids are replaced by the
    restrictive / supervised / cashier / manager_employee_ids roles."""
    cr = env.cr
    done = []
    for old_table, new_table in _role_mapping:
        if not openupgrade.table_exists(cr, old_table):
            continue
        exclude = " ".join(
            f"""
            AND NOT EXISTS (
                SELECT 1 FROM {table} other
                 WHERE other.pos_config_id = old.pos_config_id
                   AND other.hr_employee_id = old.hr_employee_id)
            """
            for table in done
        )
        openupgrade.logged_query(
            cr,
            f"""
            INSERT INTO {new_table} (pos_config_id, hr_employee_id)
            SELECT old.pos_config_id, old.hr_employee_id
              FROM {old_table} old
             WHERE TRUE {exclude}
            ON CONFLICT DO NOTHING
            """,
        )
        done.append(new_table)


def _pos_config_all_employees_cashiers(env):
    """In v19 an empty list of basic employees let every employee of the
    company log in (as cashier unless listed as minimal / manager). v20 only
    loads the listed employees (role 'none' otherwise). Keep the employees of
    configs with employee login enabled able to log in by listing them as
    cashiers."""
    cr = env.cr
    if not openupgrade.table_exists(cr, "pos_hr_basic_employee_hr_employee"):
        return
    openupgrade.logged_query(
        cr,
        """
        INSERT INTO pos_hr_cashier_employee_hr_employee
               (pos_config_id, hr_employee_id)
        SELECT config.id, employee.id
          FROM pos_config config
          JOIN hr_employee employee
            ON employee.company_id = config.company_id
           AND employee.active
         WHERE config.module_pos_hr
           AND NOT EXISTS (
               SELECT 1 FROM pos_hr_basic_employee_hr_employee basic
                WHERE basic.pos_config_id = config.id)
           AND NOT EXISTS (
               SELECT 1 FROM pos_hr_manager_employee_hr_employee r
                WHERE r.pos_config_id = config.id
                  AND r.hr_employee_id = employee.id)
           AND NOT EXISTS (
               SELECT 1 FROM pos_hr_restrictive_employee_hr_employee r
                WHERE r.pos_config_id = config.id
                  AND r.hr_employee_id = employee.id)
        ON CONFLICT DO NOTHING
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    _pos_config_employee_roles(env)
    _pos_config_all_employees_cashiers(env)
