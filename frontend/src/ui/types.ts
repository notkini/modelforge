export type InterpreterChoice='auto'|'gemini'|'local';
export type SettingsProvider='gemini'|'local';
export type Settings={
  provider:SettingsProvider;
  configured:boolean;
  connection_status:'ok'|'failed'|null;
};
export type Spec={
 shape_type:string;
 dimensions_mm:{width:number;depth:number;height:number};
 wall_thickness?:number|null;
 floor_thickness?:number|null;
 corner_radius?:number|null;
 hole_diameter?:number|null;
 fillet_radius?:number|null;
 chamfer_distance?:number|null;
 thickness?:number|null;
 enclosure_open?:boolean;
 lid?:boolean;
 radius?:number|null;
 lobes:unknown[];
 resolution:number;
 [key:string]:unknown
};
export type ComponentMeta={
  id:string;
  name:string;
  dimensions_mm:number[];
  position_mm:number[];
  rotation_deg:number[];
  solid_count:number;
  volume_mm3?:number|null;
  watertight?:boolean|null;
  vertices?:number[][];
  faces?:number[][];
  mesh_file:string;
};
export type ComponentPairCheck={
  component_a_id:string;
  component_a_name:string;
  component_b_id:string;
  component_b_name:string;
  status:'pass'|'fail'|'warning'|'not_checked';
  min_distance_mm?:number|null;
  requested_clearance_mm?:number|null;
  interference:boolean;
  reason?:string|null;
};
export type ChangeSummary={
  verified:boolean;
  components_added?:string[];
  components_removed?:string[];
  components_modified?:string[];
  dimensions_changed?:{old:number[];new:number[]}|null;
  validation_changed?:Record<string,{old:unknown;new:unknown}>;
  warnings?:string[];
  note?:string|null;
};
export type Model={
  model_id:string;
  spec:Spec;
  vertices?:number[][];
  faces?:number[][];
  components?:ComponentMeta[];
  validation:{
   watertight:boolean;
   winding_consistent:boolean;
   vertices:number;
   faces:number;
   dimensions_mm:number[];
   printability:string;
   warnings?:string[];
   components?:string[];
   component_count?:number;
   component_pairs?:ComponentPairCheck[];
   change_summary?:ChangeSummary;
   [key:string]:unknown
  };
  generation_method:string;
  version?:number;
  parent_version_id?:string|null;
  provider?:string;
  interpretation_provider?:string;
  geometry_generation_method?:string;
  fallback_reason?:string|null;
  message?:string;
  request_id?:string|null;
};
export type Message={
 id?:string;
 role:'user'|'assistant'|'system';
 text:string;
 model?:Model;
 metadata?:Record<string,unknown>;
 status?: 'pending' | 'complete' | 'failed' | 'cancelled';
 optimisticId?: string;
};
export type Conversation={
 id:string;
 title:string;
 messages:Message[];
 updated:number;
 archived?:boolean
};
export type VersionSummary={
 model_id:string;
 version:number;
 parent_version_id:string|null;
 parent_version:number|null;
 created_at:string;
 shape_type:string;
 generation_method:string;
 provider:string|null;
 summary:string;
 script_available:boolean;
 artifact_available:boolean;
 step_available:boolean;
 watertight:boolean;
 dimensions_mm:number[]|null;
 vertices:number|null;
 faces:number|null
};
