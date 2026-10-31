import {useEffect,useRef} from 'react';
import {X} from './icons';

type Props={
  message:string;
  type:'error'|'success';
  onDismiss:()=>void;
};

export default function Toast({message,type,onDismiss}:Props){
  const timer=useRef<ReturnType<typeof setTimeout>|null>(null);

  useEffect(()=>{
    timer.current=setTimeout(onDismiss,5000);
    return()=>{
      if(timer.current)clearTimeout(timer.current);
    };
  },[onDismiss]);

  const isError=type==='error';

  return (
    <div className={`toast toast-${type}`} role="alert" aria-live="polite">
      <span className="toast-icon">{isError?'!':'✓'}</span>
      <span className="toast-message">{message}</span>
      <button className="toast-dismiss" onClick={onDismiss} title="Dismiss"><X size={14}/></button>
    </div>
  );
}
