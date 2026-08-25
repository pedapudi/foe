#!/bin/sh
set -eu

/bin/cat > /app/gpt2.c <<'C'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#define D 768
#define N 50257
#define S 1024
#define Z 131071
static float *w,*K,*V,x[D],y[D],z[3*D],a[4*D],sc[S];
static unsigned char *vs[N];
static unsigned short vl[N];
static int sh[Z],ph[Z],L[N],R[N],bm[256],rv[324];
static unsigned hs(const unsigned char*s,int n){unsigned h=2166136261u;while(n--)h=(h^*s++)*16777619u;return h%Z;}
static int sf(const unsigned char*s,int n,int put){unsigned h=hs(s,n);for(;;h=(h+1)%Z){int q=sh[h]-1;if(q<0){if(put)sh[h]=put;return -1;}if(vl[q]==n&&!memcmp(vs[q],s,n))return q;}}
static int pf(int l,int r,int put){unsigned h=((unsigned)l*1000003u+r)%Z;for(;;h=(h+1)%Z){int q=ph[h]-1;if(q<0){if(put)ph[h]=put+1;return -1;}if(L[q]==l&&R[q]==r)return q;}}
static int dec(unsigned char*s){int i=0,n=0,c;while(s[i]){c=s[i++];if(c&128)c=(c&31)*64+(s[i++]&63);s[n++]=rv[c];}s[n]=0;return n;}
static void vocab(char*f){int id=0,k=0,b,na,nb,ia,ib;for(b=0;b<256;b++)if((b>=33&&b<=126)||(b>=161&&b<=172)||b>=174){bm[b]=id;rv[b]=b;vs[id]=malloc(1);*vs[id]=b;vl[id]=1;sf(vs[id],1,id+1);id++;}for(b=0;b<256;b++)if(!((b>=33&&b<=126)||(b>=161&&b<=172)||b>=174)){bm[b]=id;rv[256+k++]=b;vs[id]=malloc(1);*vs[id]=b;vl[id]=1;sf(vs[id],1,id+1);id++;}FILE*q=fopen(f,"rb");if(!q){perror(f);exit(1);}fseek(q,0,2);long n=ftell(q);rewind(q);unsigned char*s=malloc(n+1),*p,*e;if(fread(s,1,n,q)!=n){fprintf(stderr,"bad bpe\n");exit(1);}fclose(q);s[n]=0;p=(unsigned char*)strchr((char*)s,'\n')+1;while(id<N&&(e=(unsigned char*)strchr((char*)p,' '))){*e++=0;unsigned char*d=(unsigned char*)strchr((char*)e,'\n');if(d)*d=0;na=dec(p);nb=dec(e);ia=sf(p,na,0);ib=sf(e,nb,0);if(ia<0||ib<0){fprintf(stderr,"bad bpe\n");exit(1);}vl[id]=na+nb;vs[id]=malloc(vl[id]);memcpy(vs[id],p,na);memcpy(vs[id]+na,e,nb);L[id]=ia;R[id]=ib;sf(vs[id],vl[id],id+1);pf(ia,ib,id);id++;if(!d)break;p=d+1;}if(id==N-1){vs[id]=(unsigned char*)strdup("<|endoftext|>");vl[id++]=13;}free(s);if(id!=N){fprintf(stderr,"bad bpe count\n");exit(1);}}
static int encode(unsigned char*s,int**out){int n=strlen((char*)s),i,m,best,q;int*t=malloc((n+1)*sizeof(int)),*u=malloc((n+1)*sizeof(int));for(i=0;i<n;i++)t[i]=bm[s[i]];for(;;){best=N;for(i=0;i+1<n;i++){q=pf(t[i],t[i+1],0);if(q>=0&&q<best)best=q;}if(best==N)break;for(i=m=0;i<n;i++)if(i+1<n&&t[i]==L[best]&&t[i+1]==R[best])u[m++]=best,i++;else u[m++]=t[i];memcpy(t,u,m*sizeof(int));n=m;}free(u);*out=t;return n;}
static void mv(float*v,float*o,float*m,float*b,int n,int d){int i,j;memcpy(o,b,d*4);for(i=0;i<n;i++){float q=v[i],*r=m+(size_t)i*d;for(j=0;j<d;j++)o[j]+=q*r[j];}}
static void ln(float*v,float*o,float*g,float*b){int i;float m=0,q=0;for(i=0;i<D;i++)m+=v[i];m/=D;for(i=0;i<D;i++){o[i]=v[i]-m;q+=o[i]*o[i];}q=1/sqrtf(q/D+1e-5f);for(i=0;i<D;i++)o[i]=o[i]*q*g[i]+b[i];}
static int run(int tok,int pos){int l,i,j,h,t,ix;float *p,*M,q,sum,big;for(i=0;i<D;i++)x[i]=w[(size_t)(111774+tok)*D+i]+w[(size_t)(110750+pos)*D+i];for(l=0;l<12;l++){p=w+(size_t)(l<2?l:l<10?l+2:l-8)*9229*D;ln(x,y,p+3077*D,p+3076*D);mv(y,z,p+3*D,p,D,3*D);float*kp=K+((size_t)l*S+pos)*D,*vp=V+((size_t)l*S+pos)*D;memcpy(kp,z+D,D*4);memcpy(vp,z+2*D,D*4);for(h=0;h<12;h++){big=-1e30f;for(t=0;t<=pos;t++){q=0;kp=K+((size_t)l*S+t)*D+h*64;for(i=0;i<64;i++)q+=z[h*64+i]*kp[i];sc[t]=q*.125f;if(sc[t]>big)big=sc[t];}sum=0;for(t=0;t<=pos;t++)sum+=sc[t]=expf(sc[t]-big);for(i=0;i<64;i++){q=0;for(t=0;t<=pos;t++)q+=sc[t]*V[((size_t)l*S+t)*D+h*64+i];y[h*64+i]=q/sum;}}mv(y,z,p+2308*D,p+2307*D,D,D);for(i=0;i<D;i++)x[i]+=z[i];ln(x,y,p+3079*D,p+3078*D);mv(y,a,p+3084*D,p+3080*D,D,4*D);for(i=0;i<4*D;i++){q=a[i];a[i]=.5f*q*(1+tanhf(.79788456f*(q+.044715f*q*q*q)));}mv(a,z,p+6157*D,p+6156*D,4*D,D);for(i=0;i<D;i++)x[i]+=z[i];}ln(x,y,w+110749*D,w+110748*D);ix=0;big=-1e30f;for(j=0;j<N;j++){sum=0;M=w+(size_t)(111774+j)*D;for(i=0;i<D;i++)sum+=y[i]*M[i];if(sum>big)big=sum,ix=j;}return ix;}
int main(int c,char**v){if(c!=4)return fprintf(stderr,"usage: %s model.ckpt vocab.bpe prompt\n",v[0]),1;vocab(v[2]);int*t,n=encode((unsigned char*)v[3],&t),i,tok=0;if(!n)t[0]=N-1,n=1;if(n+19>S)return fprintf(stderr,"prompt too long\n"),1;FILE*f=fopen(v[1],"rb");if(!f)return perror(v[1]),1;w=malloc((size_t)124439808*4);K=malloc((size_t)12*S*D*4);V=malloc((size_t)12*S*D*4);if(!w||!K||!V||fread(w,4,124439808,f)!=124439808)return fprintf(stderr,"bad checkpoint\n"),1;fclose(f);fwrite(v[3],1,strlen(v[3]),stdout);for(i=0;i<n;i++)tok=run(t[i],i);for(;i<n+20;i++){fwrite(vs[tok],1,vl[tok],stdout);fflush(stdout);if(i<n+19)tok=run(tok,i);}free(t);return 0;}
C
