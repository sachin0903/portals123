"""SEI Wealth Platform sync.

SEI is a file-drop custodian, not an API one: every night it writes a set of
pipe-delimited reports to an SFTP endpoint, each PGP-encrypted to the Graphene
public key. This package moves them into the core object store and decrypts
them; `models/` then reads the decrypted CSVs into the DuckLake warehouse.
"""
