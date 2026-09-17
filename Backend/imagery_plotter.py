import pandas as pd
import matplotlib.pyplot as plt



def plotImage(img_):
    df = pd.DataFrame(img_)
    
    plt.scatter(df['lon'], df['lat'], c=df['NDVI'], cmap='YlGn', s=10)
    plt.colorbar(label='NDVI')
    plt.title("NDVI Scatter Plot")
    plt.xlabel("Longitude")
    plt.ylabel("Latitude")
    plt.show()

