"""v8 multi-hospital tenancy: organizations, hospital/organization memberships, active-hospital pointer,
organization-scoped audit rows, and PostgreSQL tenant-consistency triggers.

Data: every existing hospital is assigned to one "Default organization"; every existing user gets a hospital
membership with their current role/department (so nobody loses access). Downgrade removes the V8-only concepts:
platform/organization-level audit rows (hospital_id NULL) are deleted, users without any hospital get their first
membership back as their hospital (users with no membership at all — e.g. a platform admin — are deleted).

Revision ID: 623279e318d6
Revises: 181a85264e64
Create Date: 2026-09-23 15:06:52.075145
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '623279e318d6'
down_revision: str | None = '181a85264e64'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tenant-consistency trigger specs at this revision (generated from app.db.tenant_guard.tenant_refs, frozen here).
TENANT_REFS = {'consumables': (True, [('category_id', 'consumable_categories')]), 'supplier_products': (False, [('consumable_id', 'consumables'), ('supplier_id', 'suppliers')]), 'stock_batches': (False, [('consumable_id', 'consumables'), ('supplier_id', 'suppliers')]), 'stock_movements': (True, [('batch_id', 'stock_batches'), ('consumable_id', 'consumables'), ('department_id', 'departments'), ('supplier_id', 'suppliers')]), 'alerts': (True, [('batch_id', 'stock_batches'), ('consumable_id', 'consumables')]), 'model_item_metrics': (False, [('consumable_id', 'consumables'), ('model_version_id', 'model_versions')]), 'forecasts': (True, [('consumable_id', 'consumables'), ('model_version_id', 'model_versions')]), 'procedure_types': (True, [('department_id', 'departments')]), 'procedure_item_mappings': (True, [('consumable_id', 'consumables'), ('procedure_type_id', 'procedure_types')]), 'procedure_schedules': (True, [('department_id', 'departments'), ('procedure_type_id', 'procedure_types')]), 'stockout_predictions': (True, [('consumable_id', 'consumables'), ('forecast_model_version_id', 'model_versions'), ('risk_model_version_id', 'risk_model_versions')]), 'supplier_orders': (True, [('consumable_id', 'consumables'), ('recommendation_id', 'procurement_recommendations'), ('supplier_id', 'suppliers')]), 'supplier_deliveries': (True, [('batch_id', 'stock_batches'), ('order_id', 'supplier_orders'), ('stock_movement_id', 'stock_movements')]), 'procurement_recommendations': (True, [('consumable_id', 'consumables'), ('stockout_prediction_id', 'stockout_predictions')]), 'hospital_memberships': (True, [('department_id', 'departments')])}


def _pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table('organizations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organizations')),
    sa.UniqueConstraint('code', name=op.f('uq_organizations_code'))
    )
    bind = op.get_bind()
    has_hospitals = bind.execute(sa.text("SELECT count(*) FROM hospitals")).scalar() > 0
    if has_hospitals:
        bind.execute(sa.text(
            "INSERT INTO organizations (name, code, status, is_demo, created_at, updated_at) "
            "VALUES ('Default organization', 'DEFAULT', 'ACTIVE', false, now(), now())"))

    op.add_column('hospitals', sa.Column('organization_id', sa.Integer(), nullable=True))
    op.add_column('hospitals', sa.Column('status', sa.String(length=16), nullable=False, server_default='ACTIVE'))
    bind.execute(sa.text("UPDATE hospitals SET organization_id = (SELECT id FROM organizations WHERE code = 'DEFAULT')"))
    op.alter_column('hospitals', 'organization_id', existing_type=sa.Integer(), nullable=False)
    op.alter_column('hospitals', 'status', server_default=None)
    op.create_index(op.f('ix_hospitals_organization_id'), 'hospitals', ['organization_id'], unique=False)
    op.create_foreign_key(op.f('fk_hospitals_organization_id_organizations'), 'hospitals', 'organizations', ['organization_id'], ['id'], ondelete='RESTRICT')

    op.create_table('hospital_memberships',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('hospital_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('department_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_hospital_memberships_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_hospital_memberships_department_id_departments'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['hospital_id'], ['hospitals.id'], name=op.f('fk_hospital_memberships_hospital_id_hospitals'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_hospital_memberships_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hospital_memberships')),
    sa.UniqueConstraint('user_id', 'hospital_id', name='uq_hospital_memberships_user_hospital')
    )
    op.create_index(op.f('ix_hospital_memberships_hospital_id'), 'hospital_memberships', ['hospital_id'], unique=False)
    op.create_index(op.f('ix_hospital_memberships_user_id'), 'hospital_memberships', ['user_id'], unique=False)
    op.create_table('organization_memberships',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_organization_memberships_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_organization_memberships_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organization_memberships')),
    sa.UniqueConstraint('user_id', 'organization_id', name='uq_org_memberships_user_org')
    )
    op.create_index(op.f('ix_organization_memberships_organization_id'), 'organization_memberships', ['organization_id'], unique=False)
    op.create_index(op.f('ix_organization_memberships_user_id'), 'organization_memberships', ['user_id'], unique=False)
    # every existing user keeps exactly the access they had: one membership with their current role / department
    bind.execute(sa.text(
        "INSERT INTO hospital_memberships (user_id, hospital_id, role, department_id, status, created_at, updated_at) "
        "SELECT id, hospital_id, role, department_id, 'ACTIVE', created_at, now() FROM users WHERE hospital_id IS NOT NULL"))

    op.add_column('audit_logs', sa.Column('organization_id', sa.Integer(), nullable=True))
    op.alter_column('audit_logs', 'hospital_id', existing_type=sa.INTEGER(), nullable=True)
    bind.execute(sa.text("UPDATE audit_logs SET organization_id = "
                         "(SELECT organization_id FROM hospitals h WHERE h.id = audit_logs.hospital_id)"))
    op.create_index('ix_audit_logs_organization_created', 'audit_logs', ['organization_id', 'created_at'], unique=False)
    op.create_foreign_key(op.f('fk_audit_logs_organization_id_organizations'), 'audit_logs', 'organizations', ['organization_id'], ['id'], ondelete='CASCADE')

    op.add_column('users', sa.Column('is_platform_admin', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.alter_column('users', 'hospital_id', existing_type=sa.INTEGER(), nullable=True)
    op.alter_column('users', 'role', existing_type=sa.VARCHAR(length=32), nullable=True)
    op.drop_constraint(op.f('fk_users_hospital_id_hospitals'), 'users', type_='foreignkey')
    op.create_foreign_key(op.f('fk_users_hospital_id_hospitals'), 'users', 'hospitals', ['hospital_id'], ['id'], ondelete='SET NULL')

    if _pg():
        from app.db.tenant_guard import trigger_statements

        for stmt in trigger_statements(TENANT_REFS):
            bind.execute(sa.text(stmt))


def downgrade() -> None:
    bind = op.get_bind()
    if _pg():
        from app.db.tenant_guard import drop_statements

        for stmt in drop_statements(TENANT_REFS):
            bind.execute(sa.text(stmt))
    # users: restore a home hospital from the first membership; accounts without any hospital cannot exist before V8
    bind.execute(sa.text(
        "UPDATE users SET hospital_id = (SELECT m.hospital_id FROM hospital_memberships m WHERE m.user_id = users.id "
        "ORDER BY m.id LIMIT 1), role = COALESCE(role, (SELECT m.role FROM hospital_memberships m "
        "WHERE m.user_id = users.id ORDER BY m.id LIMIT 1)) WHERE hospital_id IS NULL OR role IS NULL"))
    bind.execute(sa.text("DELETE FROM users WHERE hospital_id IS NULL"))
    bind.execute(sa.text("UPDATE users SET role = 'viewer' WHERE role IS NULL"))
    op.drop_constraint(op.f('fk_users_hospital_id_hospitals'), 'users', type_='foreignkey')
    op.create_foreign_key(op.f('fk_users_hospital_id_hospitals'), 'users', 'hospitals', ['hospital_id'], ['id'], ondelete='CASCADE')
    op.alter_column('users', 'role', existing_type=sa.VARCHAR(length=32), nullable=False)
    op.alter_column('users', 'hospital_id', existing_type=sa.INTEGER(), nullable=False)
    op.drop_column('users', 'is_platform_admin')

    bind.execute(sa.text("DELETE FROM audit_logs WHERE hospital_id IS NULL"))
    op.drop_constraint(op.f('fk_audit_logs_organization_id_organizations'), 'audit_logs', type_='foreignkey')
    op.drop_index('ix_audit_logs_organization_created', table_name='audit_logs')
    op.alter_column('audit_logs', 'hospital_id', existing_type=sa.INTEGER(), nullable=False)
    op.drop_column('audit_logs', 'organization_id')

    op.drop_index(op.f('ix_organization_memberships_user_id'), table_name='organization_memberships')
    op.drop_index(op.f('ix_organization_memberships_organization_id'), table_name='organization_memberships')
    op.drop_table('organization_memberships')
    op.drop_index(op.f('ix_hospital_memberships_user_id'), table_name='hospital_memberships')
    op.drop_index(op.f('ix_hospital_memberships_hospital_id'), table_name='hospital_memberships')
    op.drop_table('hospital_memberships')

    op.drop_constraint(op.f('fk_hospitals_organization_id_organizations'), 'hospitals', type_='foreignkey')
    op.drop_index(op.f('ix_hospitals_organization_id'), table_name='hospitals')
    op.drop_column('hospitals', 'status')
    op.drop_column('hospitals', 'organization_id')
    op.drop_table('organizations')
