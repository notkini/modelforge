import {useCallback,useEffect,useRef,useState} from 'react';
import {X} from './icons';
import {Conversation} from './types';

type Props={
  open:boolean;
  conversation:Conversation;
  onClose:()=>void;
  onRename:(id:string,title:string)=>Promise<void>;
};

export default function RenameConversationDialog({open,conversation,onClose,onRename}:Props){
  const [title,setTitle]=useState(conversation.title);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const inputRef=useRef<HTMLInputElement>(null);
  const previousFocus=useRef<HTMLElement|null>(null);

  const handleClose=useCallback(()=>{
    setError('');
    setBusy(false);
    setTitle(conversation.title);
    onClose();
    if(previousFocus.current)previousFocus.current.focus();
  },[conversation.title,onClose]);

  useEffect(()=>{
    if(open){
      previousFocus.current=document.activeElement as HTMLElement|null;
      setTitle(conversation.title);
      setError('');
      setBusy(false);
      const input=inputRef.current;
      if(input){
        input.focus();
        input.select();
      }
    }
    return()=>{
      previousFocus.current=null;
    };
  },[open,conversation.title]);

  const maxLen=240;

  const submit=useCallback(async()=>{
    const t=title.trim();
    if(!t){
      setError('Conversation title cannot be empty.');
      return;
    }
    if(t.length>maxLen){
      setError(`Title too long (max ${maxLen} characters).`);
      return;
    }
    setBusy(true);
    setError('');
    try{
      await onRename(conversation.id,t);
    }catch(e){
      setError((e as Error).message||'Rename failed.');
    }finally{
      setBusy(false);
    }
  },[title,conversation.id,onRename,maxLen]);

  const onKeyDown=(e:React.KeyboardEvent)=>{
    if(e.key==='Enter'){e.preventDefault();submit();}
    if(e.key==='Escape'){handleClose();}
  };

  if(!open)return null;

  return (
    <div className="dialog-overlay" onClick={handleClose}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="rename-dialog-title">
        <div className="dialog-header">
          <h2 id="rename-dialog-title">Rename conversation</h2>
          <button className="dialog-close" onClick={handleClose} title="Close"><X size={16}/></button>
        </div>
        <div className="dialog-body">
          <label>Conversation name</label>
          <input
            ref={inputRef}
            type="text"
            value={title}
            onChange={e=>setTitle(e.target.value)}
            maxLength={maxLen}
            onKeyDown={onKeyDown}
            disabled={busy}
            placeholder="Enter a name"
          />
          {error&&<div className="dialog-error" role="alert">{error}</div>}
          <div className="dialog-hint">{title.length}/{maxLen} characters</div>
        </div>
        <div className="dialog-footer">
          <button className="dialog-button dialog-button-secondary" onClick={handleClose} disabled={busy} title="Cancel">Cancel</button>
          <button className="dialog-button dialog-button-primary" onClick={submit} disabled={busy||!title.trim()} title="Save">{busy?'Saving…':'Save'}</button>
        </div>
      </div>
    </div>
  );
}
