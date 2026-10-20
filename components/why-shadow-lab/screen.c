/* Terminal-return histogram screen. This is NOT a path-aware backtest. */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>

typedef struct { int64_t id; double score; } Pick;
static int desc(const void *a,const void *b) {
    double x=((const Pick*)a)->score,y=((const Pick*)b)->score;
    return x>y?-1:x<y?1:0;
}
int64_t screen(const double *hist,const double *counts,int bases,
               int64_t start,int64_t requested,int bins,double cost_bps,
               const double *weights,int keep,int64_t *ids,double *scores) {
    const int grid=80; const int64_t per=grid*grid,total=(int64_t)bases*per;
    int64_t end=start+requested; if(end>total)end=total;
    if(start>=total)return 0;
    Pick *p=calloc(keep,sizeof(Pick)); if(!p)return -1;
    for(int j=0;j<keep;j++){p[j].score=-INFINITY;p[j].id=-1;}
    int worst=0; int64_t scored=0;
    for(int64_t id=start;id<end;id++) {
        int base=(int)(id/per),tp_i=(int)(id%per/grid),sl_i=(int)(id%grid);
        double n=counts[base]; if(n<20)continue;
        scored++;
        double tp=15.0+5.0*tp_i,sl=15.0+5.0*sl_i,sum=0,sum2=0;
        const double *h=hist+(size_t)base*bins;
        for(int b=0;b<bins;b++) {
            if(h[b]==0)continue;
            double x=-800.0+1600.0*b/(bins-1);
            x=fmin(tp,fmax(-sl,x))-cost_bps;
            sum+=h[b]*x;sum2+=h[b]*x*x;
        }
        double mean=sum/n,var=fmax(1.0,sum2/n-mean*mean);
        double score=(mean/sqrt(var))*sqrt(fmin(n,100.0))*weights[base];
        if(score>p[worst].score) {
            p[worst]=(Pick){id,score}; worst=0;
            for(int j=1;j<keep;j++)if(p[j].score<p[worst].score)worst=j;
        }
    }
    qsort(p,keep,sizeof(Pick),desc);
    for(int j=0;j<keep;j++){ids[j]=p[j].id;scores[j]=p[j].score;}
    free(p);return scored;
}
