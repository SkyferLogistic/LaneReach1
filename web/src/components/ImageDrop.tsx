/** Drop or choose one image file (PNG, JPEG, GIF or WebP). */
import { useRef, useState } from "react";
import { Icon } from "./icons";

export function ImageDrop({
  file,
  onFile,
  label = "Drop the signature picture",
}: {
  file: File | null;
  onFile: (file: File) => void;
  label?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      className={over ? "dropzone over" : "dropzone"}
      role="button"
      tabIndex={0}
      onClick={() => input.current?.click()}
      onKeyDown={(event) => (event.key === "Enter" || event.key === " ") && input.current?.click()}
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        const dropped = event.dataTransfer.files[0];
        if (dropped) onFile(dropped);
      }}
    >
      <Icon name={file ? "image" : "upload"} />
      <div>{file ? <strong>{file.name}</strong> : <><strong>{label}</strong> or click to choose</>}</div>
      <div className="small">PNG, JPEG, GIF or WebP · up to 5 MB</div>
      <input ref={input} type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden onChange={(event) => event.target.files?.[0] && onFile(event.target.files[0])} />
    </div>
  );
}
