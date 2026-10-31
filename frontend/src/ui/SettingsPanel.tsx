import {useCallback,useEffect,useRef,useState} from 'react';
import {X} from './icons';
import {api} from './apiClient';
import {Settings,SettingsProvider} from './types';

export default function SettingsPanel({open,onClose,onSaved}:{open:boolean;onClose:()=>void;onSaved?:()=>void}){
  const [settings,setSettings]=useState<Settings|null>(null);
  const [provider,setProvider]=useState<SettingsProvider>('local');
  const [key,setKey]=useState('');
  const [saving,setSaving]=useState(false);
  const [testing,setTesting]=useState(false);
  const [error,setError]=useState('');
  const [result,setResult]=useState<'idle'|'ok'|'failed'>('idle');
  const keyInput=useRef<HTMLInputElement>(null);

  useEffect(()=>{
    if(!open){setResult('idle');setError('');setProvider('local');setKey('');return;}
    (async()=>{
      try{
        const s=await api<Settings>('/api/settings');
        setSettings(s);
        setProvider(s.provider);
      }catch(e){setError((e as Error).message||'Failed to load settings.');}
    })();
  },[open]);

  const save=useCallback(async()=>{
    setError('');
    try{
      setSaving(true);
      await api('/api/settings',{provider,api_key:provider==='gemini'?key||undefined:undefined},'PATCH');
      setKey('');
      keyInput.current?.blur();
      setResult('idle');
      if(settings)setSettings({...settings,provider,configured:provider==='gemini'&&!!key});
      onSaved?.();
    }catch(e){setError((e as Error).message||'Save failed.');}finally{setSaving(false);}
  },[key,provider,settings,onSaved]);

  const testConnection=useCallback(async()=>{
    setError('');setResult('idle');
    if(provider!=='gemini'){setResult('ok');if(settings)setSettings({...settings,connection_status:'ok'});return;}
    try{
      setTesting(true);
      const r=await api<{status:string;message:string}>('/api/settings/test',{provider:'gemini',api_key:key||undefined},'POST');
      const ok=r.status==='ok';
      setResult(ok?'ok':'failed');
      if(settings)setSettings({...settings,connection_status:ok?'ok':'failed'});
      if(!ok)setError(r.message||'Connection test failed.');
    }catch(e){setResult('failed');setError((e as Error).message||'Connection test failed.');}finally{setTesting(false);}
  },[key,provider,settings]);

  const configured=settings?.configured||false;
  const connection=settings?.connection_status||null;

  return (
    <div className="dialog-overlay" onClick={onClose} onKeyDown={e=>{if(e.key==='Escape')onClose();}}>
      <div className="dialog" onClick={e=>e.stopPropagation()}>
        <div className="dialog-header">
          <h2>Provider Settings</h2>
          <button className="dialog-close" onClick={onClose}><X size={16}/></button>
        </div>
        <div className="dialog-body">
          <label>Provider</label>
          <select value={provider} onChange={e=>setProvider(e.target.value as SettingsProvider)} disabled={saving||testing}>
            <option value="local">Local (no key needed)</option>
            <option value="gemini">Gemini</option>
          </select>
          <small className="dialog-hint">Only Gemini has backend integration. OpenAI and Anthropic appear in the list but are not yet enabled.</small>

          <label>Gemini API key</label>
          <input
            ref={keyInput}
            type="password"
            value={key}
            onChange={e=>setKey(e.target.value)}
            placeholder="Enter your Gemini API key"
            disabled={saving||testing||provider!=='gemini'}
          />
          {configured&&<span className="dialog-hint">A key is already stored. Re-enter to replace it.</span>}
          {connection&&<span className="dialog-hint">Last connection test: {connection}.</span>}

          <div className="settings-actions">
            <button
              className={`dialog-button ${result==='ok'?'dialog-button-primary':'dialog-button-secondary'} ${result==='failed'?'dialog-button-danger':''}`}
              disabled={!key||testing}
              onClick={testConnection}
            >
              {testing?'Testing…':'Test connection'}
            </button>
            <button className="dialog-button dialog-button-primary" disabled={saving} onClick={save}>
              {saving?'Saving…':'Save'}
            </button>
          </div>
          {error&&<div className="dialog-error">{error}</div>}
          {result==='ok'&&!error&&<div className="dialog-hint">Connection test succeeded.</div>}
          {result==='ok'&&!error&&configured&&<div className="dialog-hint">Settings saved.</div>}
        </div>
        <div className="dialog-footer">
          <button className="dialog-button dialog-button-secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
