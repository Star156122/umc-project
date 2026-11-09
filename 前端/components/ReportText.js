import React from 'react';
import {Text,View} from 'react-native';
export default function ReportText({text=''}){
 const blocks=String(text).split(/\n\s*\n/).filter(Boolean);
 return <View style={{gap:12}}>{blocks.map((block,index)=><View key={index} style={{padding:18,borderRadius:12,backgroundColor:'#122738',borderLeftWidth:3,borderLeftColor:'#4a9bdf',gap:8}}>{block.split('\n').map((line,i)=>{const heading=/^\s*(#{1,6}\s|【|\*\*[^*]+\*\*\s*$)/.test(line);return <Text selectable key={i} style={{color:heading?'#8ecaff':'#dce7f5',fontSize:heading?18:15,fontWeight:heading?'700':'400',lineHeight:27}}>{line.replace(/^\s*#{1,6}\s*/,'').replace(/\*\*/g,'').replace(/^\s*[-*]\s/,'• ')}</Text>;})}</View>)}</View>;
}
