# language: fr
# KAN-103, pilote (b) : les rôles d'un projet partagé, avec les comptes. Chaque personne a sa
# propre adresse IP (X-Forwarded-For) et sa propre adresse e-mail par scénario.
@kan-103
Fonctionnalité: Les rôles d'un projet partagé
  Alice partage son projet par des liens d'invitation : Bruno comme éditeur, Chloé comme
  lectrice. Denis n'est pas membre.

  Contexte:
    Étant donné qu'Alice a créé le projet "Salon du pilote"
    Et qu'elle a invité Bruno comme éditeur
    Et qu'elle a invité Chloé comme lectrice

  Scénario: l'éditeur modifie le projet
    Quand Bruno ouvre le projet par son invitation
    Alors il peut importer des photos
    Et la note qu'il ajoute est enregistrée

  Scénario: la lectrice regarde sans rien modifier
    Quand Chloé ouvre le projet par son invitation
    Alors elle ne peut rien importer
    Quand elle colle un texte dans l'espace de travail
    Alors elle voit "Lecture seule : rien n'est ajouté à ce projet."
    Et rien n'est ajouté à l'espace de travail
    Et le serveur refuse la note qu'elle tente d'ajouter

  Scénario: un non-membre n'ouvre pas le projet
    Quand Denis, qui n'est pas membre, ouvre l'adresse du projet
    Alors le serveur lui refuse le projet
    Et l'espace de travail ne s'ouvre pas pour lui
