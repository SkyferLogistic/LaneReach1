/** Sun/moon switch for the public pages (website and sign-in). */
import { Icon } from "./icons";

export function ThemeSwitch({ light, onToggle }: { light: boolean; onToggle: () => void }) {
  const label = light ? "Switch to dark theme" : "Switch to light theme";
  return (
    <button type="button" className="theme-switch" onClick={onToggle} aria-label={label} title={label}>
      <Icon name={light ? "moon" : "sun"} />
    </button>
  );
}
