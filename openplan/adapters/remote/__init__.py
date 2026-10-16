"""Adaptateurs qui parlent au réseau. Seul ce paquet importe un client HTTP (import-linter).

Rien ici n'est appelé sans passer par `openplan.adapters.gateway.ModelGateway`, qui vérifie
le consentement et journalise l'appel.
"""
