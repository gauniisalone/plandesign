import { createContext, useContext, useEffect, useState } from "react";
import { authApi, type Me, type Role } from "../api/client";

/** B1d (note 17, § 5) : la session côté écran. Ce n'est que du confort : chaque action est de
 *  toute façon refusée par le serveur si le rôle ne suffit pas. */

export interface Account {
  me: Me;
  /** Révoque la session de ce navigateur. */
  signOut: () => Promise<void>;
  /** « Me déconnecter partout » : toutes les sessions de l'utilisateur. */
  signOutAll: () => Promise<void>;
}

/** null : mode local (pas de comptes), tout est permis. */
export const AccountContext = createContext<Account | null>(null);

export function useAccount(): Account | null {
  return useContext(AccountContext);
}

/** Lecteur (et commentateur jusqu'à B2) : ni outils, ni « Générer », ni réglages du projet. */
export const ReadOnlyContext = createContext(false);

export function useReadOnly(): boolean {
  return useContext(ReadOnlyContext);
}

export function canEdit(role: Role | null): boolean {
  return role === "editor" || role === "owner";
}

/** Rôle sur le projet : `owner` sans comptes ; null tant que la réponse n'est pas là. */
export function useRole(projectId: string): Role | null {
  const account = useAccount();
  const [role, setRole] = useState<Role | null>(null);
  useEffect(() => {
    if (!account) return;
    let alive = true;
    authApi.role(projectId).then(
      (r) => {
        if (alive) setRole(r.role);
      },
      // Non-membre ou erreur : rien à modifier (le serveur refuserait de toute façon).
      () => {
        if (alive) setRole("viewer");
      },
    );
    return () => {
      alive = false;
    };
  }, [projectId, account]);
  return account ? role : "owner";
}
