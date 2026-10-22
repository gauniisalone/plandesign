"""Poste de rendu d'augus (KAN-89) : `openplan worker run` prend les demandes de rendu réaliste
d'app.plandesign.fr par des appels HTTPS sortants (aucun port ouvert, ni tunnel, ni SSH), les
calcule sur le poste, un à la fois, sans réseau, et dépose l'image. Voir
docs/contributing/poste-de-rendu.md.
"""
