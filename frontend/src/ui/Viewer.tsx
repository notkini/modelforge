import {Canvas} from '@react-three/fiber';
import {Bounds, Center, Grid, OrbitControls} from '@react-three/drei';
import {useMemo} from 'react';
import * as THREE from 'three';
import {Model,ComponentMeta} from './types';
function Shape({model,wire=false}:{model:Model;wire?:boolean}){
  const geometry=useMemo(()=>{
    const g=new THREE.BufferGeometry();
    if(model.vertices&&model.faces){
      g.setAttribute('position',new THREE.Float32BufferAttribute(model.vertices.flat(),3));
      g.setIndex(model.faces.flat());
      g.computeVertexNormals()
    }
    return g
  },[model]);
  return <mesh geometry={geometry} castShadow receiveShadow>
    <meshStandardMaterial color="#b7c8bd" roughness={.48} metalness={.12} wireframe={wire} side={THREE.DoubleSide}/>
  </mesh>
}
function ComponentShape({component,wire=false,visible=true}:{component:ComponentMeta;wire?:boolean;visible?:boolean}){
  const geometry=useMemo(()=>{
    const g=new THREE.BufferGeometry();
    if(component.vertices&&component.faces){
      g.setAttribute('position',new THREE.Float32BufferAttribute(component.vertices.flat(),3));
      g.setIndex(component.faces.flat());
      g.computeVertexNormals()
    }
    return g
  },[component]);
  if(!geometry)return null;
  const [x,y,z]=component.position_mm||[0,0,0];
  const [rx,ry,rz]=component.rotation_deg||[0,0,0];
  return <mesh geometry={geometry} position={[x,y,z]} rotation={[rx*Math.PI/180,ry*Math.PI/180,rz*Math.PI/180]} castShadow receiveShadow visible={visible}>
    <meshStandardMaterial color="#b7c8bd" roughness={.48} metalness={.12} wireframe={wire} side={THREE.DoubleSide}/>
  </mesh>
}
export default function Viewer({model,wire=false,grid=true,axes=false,ortho=false,compact=false,visibleComponents}:{model:Model;wire?:boolean;grid?:boolean;axes?:boolean;ortho?:boolean;compact?:boolean;visibleComponents?:Record<string,boolean>}){
  const components=model.components||[];
  const hasComponents=components.length>0;
  return <Canvas orthographic={ortho} shadows camera={ortho?{position:[150,120,180],zoom:1.8}:{position:[150,120,180],fov:38}} dpr={[1,1.7]}>
    <color attach="background" args={[compact?'#f1f2f0':'#171a1b']}/>
    <ambientLight intensity={.85}/>
    <directionalLight position={[120,180,100]} intensity={2.2} castShadow/>
    <directionalLight position={[-100,30,-70]} intensity={.65}/>
    <Bounds fit clip observe margin={compact?1.3:1.22}>
      <Center>
        {hasComponents?components.map(comp=><ComponentShape key={comp.id} component={comp} wire={wire} visible={visibleComponents?visibleComponents[comp.id]!==false:true}/>):<Shape model={model} wire={wire}/>}
      </Center>
    </Bounds>
    {grid&&!compact&&<Grid position={[0,-model.validation.dimensions_mm[2]/2-1,0]} args={[300,300]} cellSize={10} cellThickness={.35} sectionSize={50} sectionThickness={.7} fadeDistance={500} infiniteGrid cellColor="#45494a" sectionColor="#626869"/>}
    {axes&&!compact&&<axesHelper args={[100]}/>}
    <OrbitControls makeDefault enableDamping/>
  </Canvas>;
}
