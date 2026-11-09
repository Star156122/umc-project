import {useWindowDimensions} from 'react-native';
export default function usePageLayout(){
 const {width}=useWindowDimensions();
 const compact=width<600;
 return {compact,page:{width:'100%',alignSelf:'center',paddingHorizontal:compact?12:24},card:compact?{padding:14}:null,title:compact?{fontSize:24}:null};
}
