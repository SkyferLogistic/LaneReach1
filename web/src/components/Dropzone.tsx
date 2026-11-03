/** A drop-or-click file picker. */
import { useRef, useState } from "react";
import type { DragEvent } from "react";
import { Icon } from "./icons";

export function Dropzone({ file, onFile, accept, hint }: { file: File | null; onFile: (file: File) => void; accept: string; hint: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const drop = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    const dropped = event.dataTransfer.files[0];
    if (dropped) onFile(dropped);
  };
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
      onDrop={drop}
    >
      <Icon name={file ? "file" : "upload"} />
      <div>
        {file ? (
          <strong>{file.name}</strong>
        ) : (
          <>
            <strong>Drop a file here</strong> or click to choose
          </>
        )}
      </div>
      <div className="small">{file ? `${Math.round(file.size / 1024)} KB` : hint}</div>
      <input
        ref={input}
        type="file"
        accept={accept}
        hidden
        onChange={(event) => {
          const chosen = event.target.files?.[0];
          if (chosen) onFile(chosen);
        }}
      />
    </div>
  );
}
