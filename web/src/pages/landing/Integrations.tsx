/** Integrations strip: what LaneReach connects to today, as icon tiles (names only, no third-party logos). */
import { Icon } from "../../components/icons";
import { INTEGRATIONS } from "../../lib/landing";

export function Integrations({ onContact }: { onContact: () => void }) {
  return (
    <ul className="integrations">
      {INTEGRATIONS.map((item, position) => (
        <li key={item.name} className="integration spotlight" style={{ ["--i" as string]: position }}>
          <span className="integration-icon">
            <Icon name={item.icon} />
          </span>
          <span className="integration-kind">
            <i aria-hidden /> {item.kind}
          </span>
          <strong>{item.name}</strong>
          <p>{item.text}</p>
        </li>
      ))}
      <li className="integration more" style={{ ["--i" as string]: INTEGRATIONS.length }}>
        <span className="integration-icon">
          <Icon name="plus" />
        </span>
        <strong>Something else?</strong>
        <p>Signed webhooks reach your own systems. Tell us what you use and we will say honestly whether it fits.</p>
        <button type="button" className="site-btn ghost sm" onClick={onContact}>
          Ask us <Icon name="send" />
        </button>
      </li>
    </ul>
  );
}
