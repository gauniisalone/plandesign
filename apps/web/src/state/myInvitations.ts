import { useCallback, useEffect, useState } from "react";
import { myInvitationsApi, type MyInvitation } from "../api/client";
import { ROLE_LABEL } from "./account";

/** KAN-55 : les invitations nominatives qui m'attendent (comptes seulement), relues à chaque
 *  avis de la cloche (`tick`) : le serveur la fait sonner quand on m'invite. */

export function invitationText(i: MyInvitation): string {
  return `${i.inviter_name} vous invite sur « ${i.project_name} » (${ROLE_LABEL[i.role].toLowerCase()})`;
}

export function useMyInvitations(
  accounts: boolean,
  tick: number,
): { list: MyInvitation[]; reload: () => void } {
  const [list, setList] = useState<MyInvitation[]>([]);
  const [again, setAgain] = useState(0);
  const reload = useCallback(() => setAgain((n) => n + 1), []);
  useEffect(() => {
    if (!accounts) return;
    let alive = true;
    void myInvitationsApi.list().then(
      (found) => alive && setList(found),
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [accounts, tick, again]);
  return { list: accounts ? list : [], reload };
}
