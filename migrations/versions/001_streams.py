from alembic import op
import sqlalchemy as sa
revision = '001'
down_revision = None

def upgrade():
    op.create_table('streams',sa.Column('id',sa.Integer,primary_key=True),sa.Column('campaign_id',sa.Integer,nullable=False),sa.Column('name',sa.String,nullable=False),sa.Column('remote',sa.JSON,nullable=False),sa.Column('baseline',sa.JSON,nullable=False),sa.Column('draft',sa.JSON,nullable=False),sa.Column('version',sa.Integer,nullable=False))
    op.create_index('ix_streams_campaign_id','streams',['campaign_id'])

def downgrade():
    op.drop_index('ix_streams_campaign_id',table_name='streams')
    op.drop_table('streams')
