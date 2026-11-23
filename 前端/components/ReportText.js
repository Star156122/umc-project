import React from 'react';
import {Text,View} from 'react-native';
import {colors,radius,space,type} from '../styles/theme';
export default function ReportText({text=''}){
 const blocks=String(text).split(/\n\s*\n/).filter(Boolean);
 return <View style={{gap:12}}>{blocks.map((block,index)=><View key={index} style={{padding:space.md,borderRadius:radius.md,backgroundColor:colors.surfaceSunken,borderWidth:1,borderColor:colors.borderSoft,borderLeftWidth:3,borderLeftColor:colors.primary,gap:6}}>{block.split('\n').map((line,i)=>{const heading=/^\s*(#{1,6}\s|【|\*\*[^*]+\*\*\s*$)/.test(line);return <Text selectable key={i} style={heading?{...type.heading,fontSize:17,color:colors.primaryText}:{...type.body,lineHeight:26}}>{line.replace(/^\s*#{1,6}\s*/,'').replace(/\*\*/g,'').replace(/^\s*[-*]\s/,'• ')}</Text>;})}</View>)}</View>;
}
