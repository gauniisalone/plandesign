"""Comptes, sessions et droits par projet (note 17). Importé seulement quand
`OPENPLAN_DATABASE_URL` est défini : le mode local n'en charge rien (ni SQLAlchemy, ni base).
Dépendances : extra `comptes` (SQLAlchemy 2, psycopg 3, Alembic)."""
