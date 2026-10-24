export type InterpreterChoice='auto'|'gemini'|'local';
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
export type Model={
 model_id:string;
 spec:Spec;
 vertices?:number[][];
 faces?:number[][];
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
  [key:string]:unknown
 };
 generation_method:string;
 version?:number;
 parent_version_id?:string|null;
 provider?:string;
 interpretation_provider?:string;
 geometry_generation_method?:string;
 fallback_reason?:string|null;
 message?:string
};
export type Message={
 id?:string;
 role:'user'|'assistant'|'system';
 text:string;
 model?:Model;
 metadata?:Record<string,unknown>
};
export type Conversation={
 id:string;
 title:string;
 messages:Message[];
 updated:number;
 archived?:boolean
};
