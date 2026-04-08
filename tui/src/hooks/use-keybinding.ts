import { useEffect, useRef } from "react";
import { useKeybindingContext } from "../context/KeybindingContext.js";

export function useKeybinding(
  id: string,
  handler: (input: string, key: Record<string, boolean>) => boolean
) {
  const { register, unregister } = useKeybindingContext();
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    const wrapped = (input: string, key: Record<string, boolean>) =>
      handlerRef.current(input, key);
    register(id, wrapped);
    return () => unregister(id);
  }, [id, register, unregister]);
}
