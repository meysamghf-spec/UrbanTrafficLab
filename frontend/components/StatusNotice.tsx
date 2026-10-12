export type NoticeState = "loading" | "error" | "empty" | "success" | "info";

export function StatusNotice({ state, message }: { state: NoticeState; message: string }) {
  return (
    <div className={`notice notice-${state}`} role={state === "error" ? "alert" : "status"}>
      <span className="notice-label">{state}</span>
      <p>{message}</p>
    </div>
  );
}
