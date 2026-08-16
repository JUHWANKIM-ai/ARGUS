import type { Correction } from "../lib/types";
import { reasonText, won, ymd } from "../lib/format";
import Link from "next/link";

/**
 * 정정 하나를 한 덩이로 보여 준다 — **원공시 → 정정본** 과 금액 변화.
 *
 * 🔴 **`resolved:false` 를 감추지 않는다.** 추적 실패를 빼면 목록이 실제보다 완전해 보이고,
 * 그러면 사람은 "여기 없으니 정정이 없구나" 라고 잘못 읽는다. 사유까지 적는다.
 *
 * 🔴 접수번호를 그대로 쓴다 — DART 에서 그 번호로 원문을 찾을 수 있어야 근거가 된다.
 */
export default function CorrectionChain({
  correction,
  showCompany = false,
}: {
  correction: Correction;
  showCompany?: boolean;
}) {
  const { amount_before, amount_after, resolved, origin, targets } = correction;
  const changed = amount_before != null && amount_after != null && amount_before !== amount_after;
  const same = amount_before != null && amount_after != null && amount_before === amount_after;

  return (
    <li className="fix">
      <div className="fix-head">
        <span className="tag-corrected">정정</span>
        <span className="numeric muted">{ymd(correction.date)}</span>
        {showCompany ? (
          correction.slug ? (
            <Link className="link" href={`/company/${correction.slug}/`}>
              {correction.corp_name}
            </Link>
          ) : (
            <strong>{correction.corp_name}</strong>
          )
        ) : null}
        <span className="fix-title">{correction.title ?? correction.form_name ?? "정정 공시"}</span>
      </div>

      <div className="fix-body">
        {changed ? (
          <p className="fix-amount">
            <s>{won(amount_before)}</s> <span className="arrow">→</span> <strong>{won(amount_after)}</strong>
          </p>
        ) : same ? (
          <p className="muted">
            금액은 그대로입니다 (<span className="numeric">{won(amount_after)}</span>) — 바뀐 것은 다른 항목입니다.
          </p>
        ) : resolved ? (
          <p className="muted">원공시를 찾았지만 두 공시 중 금액이 적히지 않은 쪽이 있어 비교하지 않았습니다.</p>
        ) : (
          // 🔴 추적 실패. 이 줄이 이 서비스의 정직성이다.
          <p>
            <span className="tag-unknown">추적 실패</span>{" "}
            <span className="muted">원공시를 찾지 못했습니다 — {reasonText(correction.reason)}</span>
          </p>
        )}

        <div className="chain">
          <span>정정본</span>
          <code>{correction.rcept_no}</code>
          {origin ? (
            <>
              <span className="arrow">←</span>
              <span>원공시</span>
              <code>{origin}</code>
            </>
          ) : null}
          {targets.length > 0 ? (
            <span className="muted">
              · 이 공시가 가리키는 공시 {targets.length}건
              {!origin && targets.length > 1 ? " (하나로 확정하지 않았습니다)" : ""}
            </span>
          ) : null}
        </div>
      </div>
    </li>
  );
}
