import {useCallback,useEffect,useRef,useState} from 'react';
import {Trash2,X} from './icons';
import {Conversation} from './types';

type Props={
  open:boolean;
  conversation:Conversation;
  onClose:()=>void;
  onDelete:(id:string)=>Promise<void>;
};

export default function DeleteConversationDialog({open,conversation,onClose,onDelete}:Props){
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const previousFocus=useRef<HTMLElement|null>(null);

  const handleClose=useCallback(()=>{
    setError('');
    setBusy(false);
    onClose();
    if(previousFocus.current)previousFocus.current.focus();
  },[onClose]);

  useEffect(()=>{
    if(open){
      previousFocus.current=document.activeElement as HTMLElement|null;
      setError('');
      setBusy(false);
    }
    return()=>{
      previousFocus.current=null;
    };
  },[open]);

  const confirm=useCallback(async()=>{
    setBusy(true);
    setError('');
    try{
      await onDelete(conversation.id);
    }catch(e){
      setError((e as Error).message||'Delete failed.');
    }finally{
      setBusy(false);
    }
  },[conversation.id,onDelete]);

  const onKeyDown=(e:React.KeyboardEvent)=>{
    if(e.key==='Escape'){handleClose();}
  };

  if(!open)return null;

  return (
    <div className="dialog-overlay" onClick={handleClose} onKeyDown={onKeyDown}>
      <div className="dialog dialog-delete" role="dialog" aria-modal="true" aria-labelledby="delete-dialog-title">
        <div className="dialog-header">
          <h2 id="delete-dialog-title">Delete conversation</h2>
          <button className="dialog-close" onClick={handleClose} title="Close"><X size={16}/></button>
        </div>
        <div className="dialog-body">
          <div className="dialog-delete-icon"><Trash2 size={24}/></div>
          <p>Delete “{conversation.title}”?</p>
          <p className="dialog-delete-note">This conversation and all of its model versions will be permanently removed. This cannot be undone.</p>
          {error&&<div className="dialog-error" role="alert">{error}</div>}
        </div>
        <div className="dialog-footer">
          <button className="dialog-button dialog-button-secondary" onClick={handleClose} disabled={busy} title="Cancel">Cancel</button>
          <button className="dialog-button dialog-button-danger" onClick={confirm} disabled={busy} title="Delete">{busy?'Deleting…':'Delete'}</button>
        </div>
      </div>
    </div>
  );
}
